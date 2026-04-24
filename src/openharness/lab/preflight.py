"""Deterministic git preflight + worktree management for the lab pipeline.

The autonomous lab loop runs experiments by branching off ``main``
into an isolated worktree. This module owns every git
operation in that path: it never spawns a codex agent, never touches a
markdown file, and refuses to do anything if the parent repo is in an
ambiguous state.

Why a separate module:

-   The phase-0 contract is "the parent repo is on a clean, synced
    `main`; the worktree exists on a known branch at a recorded SHA".
    Failing fast here costs nothing; failing inside an LLM spawn costs
    money and produces noisy logs.
-   Worktree create/remove is a primitive several phases need:
    preflight creates one, finalize removes one (after Reject/NoOp), the
    operator-facing ``lab preflight`` CLI lets a human do either.
-   The base-branch policy is intentionally strict: experiments always
    fork from ``main`` so each merged experiment PR is the only way the
    daemon advances shared state.

Layout:

    <repo_root>/                                # parent repo
    <repo_root>/../OpenHarness.worktrees/       # sibling directory
        lab-<slug>/                             # one worktree per experiment
            (branch: lab/<slug>; base: <recorded SHA>)

The sibling location is a hard convention rather than a choice: putting
worktrees inside the repo would (a) confuse `git status` on the parent
and (b) recurse into themselves under tools like ripgrep. Anything under
``../OpenHarness.worktrees/`` is owned by this module — humans should
not edit it directly.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from openharness.lab.paths import REPO_ROOT

logger = logging.getLogger(__name__)


WORKTREES_ROOT: Path = REPO_ROOT.parent / f"{REPO_ROOT.name}.worktrees"
"""Sibling directory holding all per-experiment worktrees."""

DEFAULT_BRANCH_PREFIX: str = "lab/"
"""All experiment branches are named ``lab/<slug>``."""

DEFAULT_BASE_BRANCH: str = "main"
"""Experiments always fork from ``main`` under the refactored loop."""


class PreflightError(RuntimeError):
    """Base class for all preflight failures.

    Subclasses carry enough context that the orchestrator can format a
    one-line operator-facing summary (the value goes into the daemon's
    history panel) without re-running git itself.
    """


class DirtyRepoError(PreflightError):
    """Parent repo has uncommitted changes outside ``lab/`` markdowns."""


class UnpushedHeadError(PreflightError):
    """HEAD is ahead of its upstream and the operator asked to enforce push."""


class NoUpstreamError(PreflightError):
    """The current branch has no upstream and no fallback was configured."""


class WorktreeExistsError(PreflightError):
    """Asked to create a worktree that already exists at a different SHA."""


class WrongBaseBranchError(PreflightError):
    """Parent repo is not checked out on the required base branch."""


class DivergedBaseBranchError(PreflightError):
    """Local ``main`` is ahead of or diverged from ``origin/main``."""


@dataclass(slots=True, frozen=True)
class WorktreeInfo:
    """The disk artifact a successful preflight produces."""

    slug: str
    path: Path
    branch: str
    base_sha: str
    base_branch: str


# ---------------------------------------------------------------------------
# git invocation helper
# ---------------------------------------------------------------------------


def _git(
    args: list[str],
    *,
    cwd: Path = REPO_ROOT,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run ``git`` with text mode, decoded utf-8, and a deterministic env.

    We strip the inherited ``GIT_*`` environment because some shells
    (notably the codex parent) leak ``GIT_DIR`` / ``GIT_WORK_TREE``
    pointing at unrelated checkouts. Without this, ``git worktree add``
    against ``REPO_ROOT`` will silently target the wrong repo.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=capture,
        check=False,
    )
    if check and proc.returncode != 0:
        raise PreflightError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()}"
        )
    return proc


# ---------------------------------------------------------------------------
# Parent-repo state
# ---------------------------------------------------------------------------


def current_branch(repo_root: Path = REPO_ROOT) -> str:
    """Return the symbolic HEAD branch (or ``"HEAD"`` if detached)."""
    proc = _git(["symbolic-ref", "--quiet", "--short", "HEAD"],
                cwd=repo_root, check=False)
    if proc.returncode != 0:
        return "HEAD"
    return proc.stdout.strip()


def head_sha(repo_root: Path = REPO_ROOT) -> str:
    return _git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip()


def _porcelain_dirty_paths(repo_root: Path) -> list[str]:
    """Return paths reported by ``git status --porcelain`` (may be empty)."""
    proc = _git(["status", "--porcelain"], cwd=repo_root)
    out: list[str] = []
    for line in proc.stdout.splitlines():
        # porcelain format is "XY <path>" with X/Y status codes.
        # We just want the path; everything past the first space, after
        # eating the two status chars and the separator.
        if len(line) >= 4:
            out.append(line[3:])
    return out


def _is_lab_markdown(path: str) -> bool:
    """True for ``lab/*.md`` paths only — never directories or other files."""
    return path.startswith("lab/") and path.endswith(".md") and path.count("/") == 1


def assert_clean(
    *,
    repo_root: Path = REPO_ROOT,
    allow_lab_markdown_dirty: bool = False,
) -> None:
    """Raise :class:`DirtyRepoError` if the parent repo isn't clean.

    ``allow_lab_markdown_dirty=True`` whitelists pending edits to
    ``lab/*.md`` only — useful right after the daemon has appended a
    journal entry but before the auto-commit ran. The default refuses
    *any* dirt so a human running ``lab preflight`` from the shell
    gets the strict check.
    """
    dirty = _porcelain_dirty_paths(repo_root)
    if not dirty:
        return
    if allow_lab_markdown_dirty and all(_is_lab_markdown(p) for p in dirty):
        logger.info("parent repo has lab/*.md edits only; continuing")
        return
    raise DirtyRepoError(
        f"Parent repo {repo_root} has uncommitted changes:\n  - "
        + "\n  - ".join(dirty[:20])
        + ("\n  …(truncated)" if len(dirty) > 20 else "")
    )


def upstream_for(branch: str, repo_root: Path = REPO_ROOT) -> str | None:
    """Return ``origin/<branch>``-style upstream, or ``None`` if unset."""
    proc = _git(
        ["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"],
        cwd=repo_root, check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def assert_pushed(
    branch: str,
    *,
    repo_root: Path = REPO_ROOT,
    auto_push: bool = False,
) -> None:
    """Refuse if ``branch`` is ahead of its upstream.

    With ``auto_push=True``, attempt a ``git push`` first; raise on
    push failure (auth, rejection). With ``auto_push=False``, just
    raise :class:`UnpushedHeadError`.
    """
    upstream = upstream_for(branch, repo_root=repo_root)
    if upstream is None:
        raise NoUpstreamError(
            f"Branch {branch!r} has no upstream. Set one with "
            f"`git push -u origin {branch}` and retry."
        )
    # ``git rev-list --count <upstream>..HEAD`` = unpushed commits.
    ahead = int(_git(
        ["rev-list", "--count", f"{upstream}..HEAD"],
        cwd=repo_root,
    ).stdout.strip() or 0)
    if ahead == 0:
        return
    if not auto_push:
        raise UnpushedHeadError(
            f"Branch {branch!r} is {ahead} commit(s) ahead of "
            f"{upstream}; push first or pass --auto-push."
        )
    logger.info("auto-pushing %d commit(s) on %s to %s", ahead, branch, upstream)
    _git(["push"], cwd=repo_root)


def assert_on_branch(expected: str, *, repo_root: Path = REPO_ROOT) -> None:
    """Raise if the parent repo is not checked out on ``expected``."""
    actual = current_branch(repo_root)
    if actual != expected:
        raise WrongBaseBranchError(
            f"Parent repo must stay on {expected!r}; currently on {actual!r}. "
            f"Switch back to `{expected}` before running preflight."
        )


def sync_branch_to_origin(branch: str, *, repo_root: Path = REPO_ROOT) -> None:
    """Fast-forward ``branch`` to ``origin/<branch>`` or fail loudly.

    The daemon's source of truth is ``main`` on the remote. Local
    commits ahead of or diverged from ``origin/main`` would make the
    experiment's base ambiguous, so we refuse them instead of pushing
    directly to main.
    """
    _git(["fetch", "origin", branch], cwd=repo_root)
    proc = _git(
        ["rev-list", "--left-right", "--count", f"{branch}...origin/{branch}"],
        cwd=repo_root,
    )
    left_s, right_s = (proc.stdout.strip() or "0\t0").split()
    local_only = int(left_s)
    remote_only = int(right_s)
    if local_only and remote_only:
        raise DivergedBaseBranchError(
            f"Local {branch!r} diverged from origin/{branch}; reconcile it "
            "manually before running the lab daemon."
        )
    if local_only:
        raise DivergedBaseBranchError(
            f"Local {branch!r} is {local_only} commit(s) ahead of origin/{branch}. "
            "The daemon will not push directly to main; merge or reset these "
            "changes manually first."
        )
    if remote_only:
        _git(["merge", "--ff-only", f"origin/{branch}"], cwd=repo_root)
        logger.info("fast-forwarded %s to origin/%s", branch, branch)


# ---------------------------------------------------------------------------
# Worktree lifecycle
# ---------------------------------------------------------------------------


def _worktree_path_for(slug: str) -> Path:
    return WORKTREES_ROOT / f"lab-{slug}"


def _branch_name_for(slug: str) -> str:
    return f"{DEFAULT_BRANCH_PREFIX}{slug}"


def list_worktrees(repo_root: Path = REPO_ROOT) -> list[Path]:
    """Return the absolute paths of every registered git worktree (incl. main)."""
    proc = _git(["worktree", "list", "--porcelain"], cwd=repo_root)
    out: list[Path] = []
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            out.append(Path(line[len("worktree "):]).resolve())
    return out


def worktree_exists(slug: str, repo_root: Path = REPO_ROOT) -> bool:
    target = _worktree_path_for(slug).resolve()
    return any(p == target for p in list_worktrees(repo_root))


def _ensure_shared_runs_link(*, worktree: Path, repo_root: Path) -> None:
    """Point ``<worktree>/runs`` at the parent repo's shared ``runs/``.

    The worktree owns branch-local tracked files (source + ``lab/``),
    while the parent repo owns machine-local run artifacts under
    ``runs/``. A symlink keeps both truths visible when a skill
    executes ``uv run lab`` from inside the worktree.
    """
    target = repo_root / "runs"
    link = worktree / "runs"
    if link.is_symlink():
        current = link.resolve(strict=False)
        if current == target.resolve():
            return
        link.unlink()
    elif link.exists():
        return
    link.symlink_to(target)
    logger.info("linked %s -> %s", link, target)


def create_worktree(
    slug: str,
    *,
    base_sha: str,
    base_branch: str,
    repo_root: Path = REPO_ROOT,
) -> WorktreeInfo:
    """Create ``../OpenHarness.worktrees/lab-<slug>`` on branch ``lab/<slug>``.

    Idempotent: if the worktree already exists at the requested SHA on
    the requested branch, return its info. If it exists at a different
    SHA, raise :class:`WorktreeExistsError` — the caller (usually the
    runner doing phase-0 resume) decides whether to reuse or destroy.

    ``base_branch`` is recorded only for traceability (the branch ref
    isn't actually used after creation; the SHA is). Pass whatever
    branch ``base_sha`` was the tip of when preflight ran.
    """
    if not WORKTREES_ROOT.exists():
        WORKTREES_ROOT.mkdir(parents=True, exist_ok=True)

    wt_path = _worktree_path_for(slug)
    branch = _branch_name_for(slug)

    if worktree_exists(slug, repo_root=repo_root):
        existing_sha = _git(["rev-parse", "HEAD"], cwd=wt_path).stdout.strip()
        if existing_sha == base_sha:
            logger.info("worktree %s already exists at %s; reusing", wt_path, base_sha[:8])
            _ensure_shared_runs_link(worktree=wt_path, repo_root=repo_root)
            return WorktreeInfo(
                slug=slug, path=wt_path, branch=branch,
                base_sha=base_sha, base_branch=base_branch,
            )
        raise WorktreeExistsError(
            f"Worktree {wt_path} already exists at {existing_sha[:8]} "
            f"but preflight asked for {base_sha[:8]}. "
            f"Remove it with `lab preflight remove {slug}` and retry."
        )

    # Branch ref may already exist if a prior worktree was removed
    # without deleting the branch. Reuse it iff it points at the same
    # SHA; otherwise force-create a fresh ref.
    branch_check = _git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_root, check=False,
    )
    branch_exists = branch_check.returncode == 0
    if branch_exists:
        existing_branch_sha = branch_check.stdout.strip()
        if existing_branch_sha == base_sha:
            args = ["worktree", "add", str(wt_path), branch]
        else:
            # Different SHA: force-recreate the branch on top of base_sha.
            _git(["branch", "-f", branch, base_sha], cwd=repo_root)
            args = ["worktree", "add", str(wt_path), branch]
    else:
        args = ["worktree", "add", "-b", branch, str(wt_path), base_sha]

    _git(args, cwd=repo_root)
    _ensure_shared_runs_link(worktree=wt_path, repo_root=repo_root)
    logger.info(
        "created worktree %s on branch %s at %s",
        wt_path, branch, base_sha[:8],
    )
    return WorktreeInfo(
        slug=slug, path=wt_path, branch=branch,
        base_sha=base_sha, base_branch=base_branch,
    )


def remove_worktree(
    slug: str,
    *,
    repo_root: Path = REPO_ROOT,
    delete_branch: bool = True,
    force: bool = True,
) -> bool:
    """Tear down a worktree and (optionally) delete the underlying branch.

    Returns ``True`` if anything was removed, ``False`` if nothing
    matched. Idempotent — safe to call after partial failures.

    ``force=True`` allows removal even if the worktree has uncommitted
    changes. The lab flow deliberately commits per logical change in
    the implement phase, so dirt at remove-time is a bug; forcing is
    the right default to keep the loop unblockable.
    """
    branch = _branch_name_for(slug)
    wt_path = _worktree_path_for(slug)
    removed_anything = False

    if worktree_exists(slug, repo_root=repo_root):
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(wt_path))
        _git(args, cwd=repo_root)
        logger.info("removed worktree %s", wt_path)
        removed_anything = True

    if delete_branch:
        proc = _git(
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=repo_root, check=False,
        )
        if proc.returncode == 0:
            _git(["branch", "-D", branch], cwd=repo_root)
            logger.info("deleted branch %s", branch)
            removed_anything = True

    return removed_anything


# ---------------------------------------------------------------------------
# The one-shot orchestrator entry point
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class PreflightResult:
    """Everything a downstream phase needs to operate on the worktree."""

    info: WorktreeInfo
    base_sha: str
    base_branch: str


def run_preflight(
    slug: str,
    *,
    repo_root: Path = REPO_ROOT,
    base_branch: str | None = DEFAULT_BASE_BRANCH,
    auto_push: bool = False,
    allow_lab_markdown_dirty: bool = False,
) -> PreflightResult:
    """The full phase-0 contract in one call.

    Steps, in order:

    1. Assert the parent repo is checked out on ``main`` (or the
       explicit ``base_branch`` override).
    2. Assert the parent repo is clean.
    3. Fast-forward the base branch to ``origin/<base_branch>``.
    4. Capture the base SHA.
    5. Create / reuse the worktree.

    Idempotent: rerunning after success returns the same result without
    side effects. Rerunning after a partial failure (e.g. dirty repo)
    raises the same error until the operator fixes it.
    """
    branch = base_branch or DEFAULT_BASE_BRANCH
    if branch == "HEAD":
        raise PreflightError(
            f"Parent repo {repo_root} is in detached HEAD; "
            "checkout a branch before running preflight."
        )

    assert_on_branch(branch, repo_root=repo_root)

    assert_clean(
        repo_root=repo_root,
        allow_lab_markdown_dirty=allow_lab_markdown_dirty,
    )

    if auto_push:
        logger.info(
            "preflight --auto-push is deprecated for the main-branch loop; "
            "ignoring and syncing %s from origin instead",
            branch,
        )
    sync_branch_to_origin(branch, repo_root=repo_root)

    base_sha = head_sha(repo_root)
    info = create_worktree(
        slug, base_sha=base_sha, base_branch=branch, repo_root=repo_root,
    )
    return PreflightResult(info=info, base_sha=base_sha, base_branch=branch)
