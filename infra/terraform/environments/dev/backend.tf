# Local state by default. The state file contains secrets (generated passwords):
# never commit it to git.
terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}

# Alternative: remote state in S3 (create the bucket and lock table first).
# Comment out the local backend above and uncomment this block.
#
# terraform {
#   backend "s3" {
#     bucket         = "my-terraform-state-bucket"
#     key            = "messaging-analysis/dev/terraform.tfstate"
#     region         = "us-east-1"
#     encrypt        = true
#     dynamodb_table = "my-terraform-locks"
#   }
# }
