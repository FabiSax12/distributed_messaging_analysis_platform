"""End-to-end run on one laptop against the compose stack (RabbitMQ,
PostgreSQL, MinIO): the real coordinator plus `fake_worker.py` workers, and
the B.6-style scenarios.

    docker compose -f infra/docker/compose.dev.yml up -d --wait
    uv run --all-packages python services/workers/scripts/local_e2e.py
    uv run --all-packages python services/workers/scripts/local_e2e.py --scenario crash

Scenarios (all by default, in this order):
  basic        .txt + .jpg + .mp3 -> completed
  unsupported  .json + .xyz + .mp4 -> pre-failed sub-task, partially_completed
  retries      workers with --fail-rate 0.5 -> some sub-task goes through
               `retrying`; every sub-task ends terminal
  crash        kill -9 the audio worker mid-task, start a new one -> the
               sub-task is retried (worker_lost) and the case completes
  restart      kill -9 the coordinator with a case in flight, start it again
               -> the case completes, no duplicated sub-tasks
  dbdown       stop the PostgreSQL container for 20 s with a case in flight
               -> the case completes without retries_exhausted

Settings come from the environment, falling back to infra/docker/dev.env.
Logs of every process go to --logs (default /tmp/dmap-e2e). Exit code 1 if
any scenario fails.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

import httpx
import psycopg

REPO = Path(__file__).resolve().parents[3]
DEV_ENV = REPO / "infra/docker/dev.env"
API_PORT = 8765
API = f"http://127.0.0.1:{API_PORT}"
TERMINAL = {"completed", "partially_completed", "failed", "cancelled"}
POOLS = ("text", "image", "audio")
CONTENT = {
    ".txt": b"[1/2/24, 10:00] Ana: hola\n",
    ".json": b'{"name": "chat", "messages": []}',
    ".jpg": b"\xff\xd8\xff\xe0fake-jpeg",
    ".mp3": b"ID3\x03fake-mp3",
    ".mp4": b"\x00\x00\x00\x18ftypmp42",
}


class ScenarioFailed(Exception):
    pass


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for line in DEV_ENV.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            env.setdefault(key.strip(), value.strip())
    # Fast failure detection and retries so the scenarios finish quickly.
    env.setdefault("HEARTBEAT_TIMEOUT_S", "20")
    env.setdefault("SWEEP_INTERVAL_S", "2")
    env.setdefault("RETRY_BASE_DELAY_S", "1")
    env.setdefault("RETRY_MAX_ATTEMPTS", "4")
    env.setdefault("HEARTBEAT_INTERVAL_S", "2")
    return env


class Stack:
    """The coordinator and fake workers as subprocesses."""

    def __init__(self, env: dict[str, str], logs: Path) -> None:
        self.env = env
        self.logs = logs
        self.coordinator: subprocess.Popen | None = None
        self.workers: dict[str, subprocess.Popen] = {}
        self._seq = 0

    def _spawn(self, name: str, args: list[str]) -> subprocess.Popen:
        self._seq += 1
        log = open(self.logs / f"{self._seq:02d}-{name}.log", "wb")  # noqa: SIM115
        return subprocess.Popen(
            ["uv", "run", "--all-packages", *args],
            cwd=REPO,
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    def start_coordinator(self) -> None:
        self.coordinator = self._spawn(
            "coordinator",
            ["uvicorn", "coordinator.main:app", "--host", "127.0.0.1", "--port", str(API_PORT)],
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{API}/healthz", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise ScenarioFailed("coordinator did not become healthy")

    def kill_coordinator(self) -> None:
        assert self.coordinator is not None
        _kill_tree(self.coordinator, signal.SIGKILL)
        self.coordinator = None

    def start_worker(self, pool: str, *extra: str) -> None:
        self.workers[pool] = self._spawn(
            f"worker-{pool}",
            ["python", "services/workers/scripts/fake_worker.py", "--pool", pool, *extra],
        )

    def start_workers(self, *extra: str) -> None:
        for pool in POOLS:
            self.start_worker(pool, *extra)
        time.sleep(5)  # connect, declare topology, first heartbeat

    def stop_workers(self) -> None:
        for proc in self.workers.values():
            _kill_tree(proc, signal.SIGTERM)
        for proc in self.workers.values():
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                _kill_tree(proc, signal.SIGKILL)
        self.workers.clear()

    def stop(self) -> None:
        self.stop_workers()
        if self.coordinator is not None:
            _kill_tree(self.coordinator, signal.SIGTERM)
            self.coordinator.wait(timeout=20)


def _children(proc: subprocess.Popen) -> list[str]:
    return subprocess.run(
        ["pgrep", "-P", str(proc.pid)], check=False, capture_output=True, text=True
    ).stdout.split()


def _kill_tree(proc: subprocess.Popen, sig: int) -> None:
    """`uv run` forks the real interpreter: signal its children too."""
    children = _children(proc)
    for pid in children:
        try:
            os.kill(int(pid), sig)
        except ProcessLookupError:
            pass
    try:
        proc.send_signal(sig)
    except ProcessLookupError:
        pass


def worker_pid(proc: subprocess.Popen) -> int:
    children = _children(proc)
    return int(children[0]) if children else proc.pid


# ---- API helpers ------------------------------------------------------------ #


def create_case(files: list[str]) -> str:
    resp = httpx.post(f"{API}/api/uploads", json={"filenames": files}, timeout=30)
    resp.raise_for_status()
    keys = []
    for entry in resp.json()["urls"]:
        suffix = PurePosixPath(entry["filename"]).suffix
        httpx.put(entry["url"], content=CONTENT.get(suffix, b"?"), timeout=30).raise_for_status()
        keys.append(entry["input_key"])
    resp = httpx.post(
        f"{API}/api/cases",
        json={"files": [{"input_key": k} for k in keys], "label": "local e2e"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    print(
        f"  case {body['case_id']}: {body['subtask_count']} sub-tasks, "
        f"{body['prefailed_count']} pre-failed"
    )
    return body["case_id"]


def get_case(case_id: str) -> dict | None:
    try:
        resp = httpx.get(f"{API}/api/cases/{case_id}", timeout=5)
    except httpx.HTTPError:
        return None  # coordinator down (restart scenario)
    if resp.status_code >= 500:
        return None  # database down (dbdown scenario)
    resp.raise_for_status()
    return resp.json()


class Watcher:
    """Polls a case and records every (status, attempt) of each sub-task."""

    def __init__(self, case_id: str) -> None:
        self.case_id = case_id
        self.history: dict[str, list[tuple[str, int]]] = {}
        self.detail: dict | None = None

    def poll_once(self) -> dict | None:
        detail = get_case(self.case_id)
        if detail is None:
            return None
        self.detail = detail
        for sub in detail["subtasks"]:
            states = self.history.setdefault(sub["subtask_id"], [])
            state = (sub["status"], sub["attempt"])
            if not states or states[-1] != state:
                states.append(state)
                error = f" ({sub['error']['code']})" if sub.get("error") else ""
                print(
                    f"    {sub['subtask_id'][-4:]} {sub['status']:<10} "
                    f"attempt={sub['attempt']} worker={sub['worker_id']}{error}"
                )
        return detail

    def wait_until(self, predicate, timeout_s: float, what: str) -> dict:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            detail = self.poll_once()
            if detail is not None and predicate(detail):
                return detail
            time.sleep(0.3)
        raise ScenarioFailed(f"timed out waiting for {what}")

    def wait_terminal(self, timeout_s: float = 120) -> dict:
        detail = self.wait_until(
            lambda d: d["case"]["status"] in TERMINAL, timeout_s, "a terminal case"
        )
        print(f"  -> case {detail['case']['status']}")
        return detail

    def saw(self, status: str) -> bool:
        return any(s == status for states in self.history.values() for s, _ in states)


def check_report(case_id: str) -> str:
    resp = httpx.get(f"{API}/api/cases/{case_id}/report", timeout=10)
    resp.raise_for_status()
    report = httpx.get(resp.json()["url"], timeout=10).json()
    print(f"  report: {report['summary']}")
    return report["summary"]


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioFailed(message)


# ---- Scenarios -------------------------------------------------------------- #


def scenario_basic(stack: Stack, env: dict[str, str]) -> None:
    stack.start_workers()
    try:
        watcher = Watcher(create_case(["chat.txt", "photo.jpg", "voice.mp3"]))
        detail = watcher.wait_terminal()
        expect(detail["case"]["status"] == "completed", "expected completed")
        check_report(watcher.case_id)
    finally:
        stack.stop_workers()


def scenario_unsupported(stack: Stack, env: dict[str, str]) -> None:
    stack.start_workers()
    try:
        watcher = Watcher(create_case(["chat.json", "notes.xyz", "clip.mp4"]))
        detail = watcher.wait_terminal()
        expect(detail["case"]["status"] == "partially_completed", "expected partially_completed")
        prefailed = [s for s in detail["subtasks"] if s["attempt"] == 0]
        expect(
            len(prefailed) == 1 and prefailed[0]["error"]["code"] == "unsupported_format",
            "expected one pre-failed unsupported_format sub-task",
        )
        check_report(watcher.case_id)
    finally:
        stack.stop_workers()


def scenario_retries(stack: Stack, env: dict[str, str]) -> None:
    stack.start_workers("--fail-rate", "0.5", "--work-s", "0.5")
    try:
        files = [f"f{i}{ext}" for i in range(3) for ext in (".txt", ".jpg", ".mp3")]
        watcher = Watcher(create_case(files))
        detail = watcher.wait_terminal(timeout_s=180)
        expect(watcher.saw("retrying"), "no sub-task went through retrying")
        retried = [s for s in detail["subtasks"] if s["attempt"] > 1]
        exhausted = [
            s for s in detail["subtasks"]
            if s.get("error") and s["error"]["code"] == "retries_exhausted"
        ]
        print(f"  {len(retried)} sub-tasks retried, {len(exhausted)} exhausted retries")
        expect(bool(retried), "expected attempt > 1 on some sub-task")
        check_report(watcher.case_id)
    finally:
        stack.stop_workers()


def scenario_crash(stack: Stack, env: dict[str, str]) -> None:
    stack.start_workers("--work-s", "6")
    try:
        watcher = Watcher(create_case(["voice.mp3", "chat.txt"]))
        watcher.wait_until(
            lambda d: any(
                s["status"] == "running" and (s["worker_id"] or "").startswith("audio")
                for s in d["subtasks"]
            ),
            30,
            "the audio sub-task to run",
        )
        victim = stack.workers.pop("audio")
        pid = worker_pid(victim)
        print(f"  kill -9 audio worker pid {pid}")
        os.kill(pid, signal.SIGKILL)
        time.sleep(1)
        stack.start_worker("audio", "--work-s", "1")
        detail = watcher.wait_terminal(timeout_s=90)
        expect(detail["case"]["status"] == "completed", "expected completed after the crash")
        audio = next(s for s in detail["subtasks"] if s["input_key"].endswith(".mp3"))
        expect(audio["attempt"] >= 2, "the crashed sub-task was not retried")
        # The sweeper marks the killed worker dead once its heartbeat is stale.
        timeout = float(env["HEARTBEAT_TIMEOUT_S"]) + 10
        deadline = time.monotonic() + timeout
        dead = []
        while time.monotonic() < deadline and not dead:
            state = httpx.get(f"{API}/api/state", timeout=5).json()
            dead = [
                w["worker_id"] for w in state["workers"]
                if w["worker_id"].endswith(f"-{pid}") and w["status"] == "dead"
            ]
            time.sleep(1)
        expect(bool(dead), f"worker with pid {pid} was not marked dead")
        print(f"  sweeper marked {dead[0]} dead")
        check_report(watcher.case_id)
    finally:
        stack.stop_workers()


def scenario_restart(stack: Stack, env: dict[str, str]) -> None:
    stack.start_workers("--work-s", "2")
    try:
        files = [f"r{i}{ext}" for i in range(3) for ext in (".txt", ".jpg", ".mp3")]
        watcher = Watcher(create_case(files))
        watcher.wait_until(
            lambda d: any(s["status"] in ("assigned", "running") for s in d["subtasks"]),
            30,
            "a sub-task in flight",
        )
        print("  kill -9 coordinator")
        stack.kill_coordinator()
        time.sleep(5)  # workers keep publishing results into the durable queue
        print("  starting coordinator again")
        stack.start_coordinator()
        detail = watcher.wait_terminal(timeout_s=120)
        expect(detail["case"]["status"] == "completed", "expected completed after restart")
        with psycopg.connect(env["DATABASE_URL"]) as conn:
            rows, distinct = conn.execute(
                "SELECT count(*), count(DISTINCT input_key) FROM subtasks WHERE case_id = %s",
                (watcher.case_id,),
            ).fetchone()
        print(f"  subtasks rows={rows} distinct inputs={distinct} files={len(files)}")
        expect(rows == distinct == len(files), "duplicated or missing sub-tasks")
        check_report(watcher.case_id)
    finally:
        stack.stop_workers()


POSTGRES_CONTAINER = "dmap-dev-postgres-1"


def scenario_dbdown(stack: Stack, env: dict[str, str]) -> None:
    stack.start_workers("--work-s", "3")
    try:
        files = [f"d{i}{ext}" for i in range(2) for ext in (".txt", ".jpg", ".mp3")]
        watcher = Watcher(create_case(files))
        watcher.wait_until(
            lambda d: any(s["status"] == "running" for s in d["subtasks"]),
            30,
            "a running sub-task",
        )
        print(f"  docker stop {POSTGRES_CONTAINER} for 20 s")
        subprocess.run(["docker", "stop", POSTGRES_CONTAINER], check=True, capture_output=True)
        time.sleep(20)
        subprocess.run(["docker", "start", POSTGRES_CONTAINER], check=True, capture_output=True)
        print("  postgres started again")
        detail = watcher.wait_terminal(timeout_s=180)
        codes = [s["error"]["code"] for s in detail["subtasks"] if s.get("error")]
        print(f"  error codes recorded: {codes or 'none'}")
        expect(detail["case"]["status"] == "completed", "expected completed after the outage")
        expect("retries_exhausted" not in codes, "a sub-task exhausted its retries")
        check_report(watcher.case_id)
    finally:
        stack.stop_workers()


SCENARIOS = {
    "basic": scenario_basic,
    "unsupported": scenario_unsupported,
    "retries": scenario_retries,
    "crash": scenario_crash,
    "restart": scenario_restart,
    "dbdown": scenario_dbdown,
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scenario", choices=SCENARIOS, action="append")
    parser.add_argument("--logs", type=Path, default=Path("/tmp/dmap-e2e"))
    args = parser.parse_args()
    args.logs.mkdir(parents=True, exist_ok=True)

    env = load_env()
    stack = Stack(env, args.logs)
    results: dict[str, str] = {}
    try:
        stack.start_coordinator()
        for name in args.scenario or list(SCENARIOS):
            print(f"== {name}")
            try:
                SCENARIOS[name](stack, env)
                results[name] = "OK"
            except (ScenarioFailed, httpx.HTTPError) as exc:
                results[name] = f"FAILED: {exc}"
            print(f"== {name}: {results[name]}")
    finally:
        stack.stop()
    print("\n".join(f"{name:<12} {result}" for name, result in results.items()))
    print(f"logs in {args.logs}")
    return 0 if all(r == "OK" for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
