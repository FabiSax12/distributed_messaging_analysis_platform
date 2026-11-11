"""Minimal authenticated identity and administrator role-management contract."""

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import ROLES
from .deps import SettingsDep

router = APIRouter()


@router.get("/me")
def me(request: Request):
    claims = request.state.identity
    return {"subject": claims["sub"], "roles": claims.get("cognito:groups", [])}


def client(settings):
    if not settings.COGNITO_USER_POOL_ID:
        raise HTTPException(503, "Cognito user pool is not configured")
    return boto3.client("cognito-idp", region_name=settings.AWS_REGION)


@router.get("/admin/users")
def users(settings: SettingsDep, cursor: str | None = None):
    args = {"UserPoolId": settings.COGNITO_USER_POOL_ID, "Limit": 30}
    if cursor:
        args["PaginationToken"] = cursor
    service = client(settings)
    result = service.list_users(**args)
    return {
        "users": [
            {
                "username": user["Username"],
                "enabled": user["Enabled"],
                "status": user["UserStatus"],
                "roles": [
                    group["GroupName"]
                    for group in service.admin_list_groups_for_user(
                        UserPoolId=settings.COGNITO_USER_POOL_ID,
                        Username=user["Username"],
                    )["Groups"]
                ],
            }
            for user in result["Users"]
        ],
        "cursor": result.get("PaginationToken"),
    }


class RoleChange(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    role: str
    enabled: bool


@router.post("/admin/roles")
def change_role(payload: RoleChange, settings: SettingsDep):
    if payload.role not in ROLES:
        raise HTTPException(422, "Unknown role")
    method = (
        "admin_add_user_to_group" if payload.enabled else "admin_remove_user_from_group"
    )
    try:
        getattr(client(settings), method)(
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Username=payload.username,
            GroupName=payload.role,
        )
    except ClientError as exc:
        raise HTTPException(502, "Cognito role update failed") from exc
    return {"updated": True}
