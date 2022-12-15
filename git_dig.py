"""Show dependencies of a commit."""


import os
import sys
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from subprocess import DEVNULL, PIPE, CalledProcessError, Popen, run
from typing import Any

import click
from colorama import Fore, init  # type: ignore

_devnull = DEVNULL
_verbose = False
_reduce_context = 2


def vprint(msg):
    """Print in verbose only."""
    if _verbose:
        print(Fore.CYAN + f"> {msg}" + Fore.RESET)


@contextmanager
def chdir(path):
    """Contextmanager to change directory."""
    cwd = os.getcwd()
    os.chdir(path)
    vprint(f"change dir {path}")
    yield
    os.chdir(cwd)
    vprint(f"restore dir {cwd}")


def args_print(args):
    """Print args of executed command."""
    if _verbose:
        args = [str(x) for x in args]
        args = " ".join(args)
        print(Fore.GREEN + f"$ {args}" + Fore.RESET)


class SPopen(Popen):
    """Inject defaults into Popen."""

    def __init__(self, *args, stderr=None, stdin=None, **kwargs):
        """Inject default into Popen."""
        args_print(args[0])
        if stderr is None:
            stderr = _devnull
        if stdin is None and "input" not in kwargs:
            stdin = _devnull
        super().__init__(*args, stderr=stderr, stdin=stdin, encoding="UTF-8", **kwargs)


def srun(*args, stdout=None, stderr=None, stdin=None, **kwargs):
    """Inject defaults into run."""
    args_print(args[0])
    if stdout is None:
        stdout = _devnull
    if stderr is None:
        stderr = _devnull
    if stdin is None and "input" not in kwargs:
        stdin = _devnull
    return run(
        *args, stdout=stdout, stderr=stderr, stdin=stdin, encoding="UTF-8", **kwargs
    )


@contextmanager
def popen(*args, expect_code=0, **kwargs):
    """Like run check but with Popen."""
    with SPopen(*args, **kwargs) as proc:
        yield proc
    if proc.returncode != expect_code:
        raise CalledProcessError(
            proc.returncode,
            args[0],
            output=proc.stdout,
            stderr=proc.stderr,
        )


@contextmanager
def diff(parent, child=None):
    """Get git rev."""
    if child is None or child == "WORKING":
        child = []
    else:
        child = [child]
    with popen(["git", "diff", parent] + child, stdout=PIPE) as proc:
        yield proc


@contextmanager
def show(rev=None):
    """Get git rev."""
    if rev is None:
        rev = []
    else:
        rev = [rev]
    with popen(["git", "show"] + rev, stdout=PIPE) as proc:
        yield proc


@contextmanager
def blame(rev, path):
    """Blame git rev/path."""
    if rev == "WORKING":
        rev = []
    else:
        rev = [rev]
    with popen(["git", "blame", "-s"] + rev + ["--", path], stdout=PIPE) as proc:
        yield proc


def print_depend(rev):
    srun(["git", "show", "--quiet", "--oneline"] + [rev], stdout=sys.stdout, check=True)


def linereader(stream):
    while True:
        line = stream.readline()
        if not line:
            return
        line = line.strip("\n")
        vprint(line)
        yield line


@dataclass(slots=True, frozen=True)
class Hunk:
    deps: set[str]
    parent: str
    child: str
    path: str
    first: tuple[int, int]
    second: tuple[int, int]
    hint: str

    @classmethod
    def from_line(cls, parent, child, path, line):
        data = [data.strip() for data in line.split("@@") if data]
        first, _, second = data[0].partition(" ")
        first = tuple([int(i) for i in first[1:].split(",")])
        second = tuple([int(i) for i in second[1:].split(",")])
        hint = ""
        if len(data) > 1:
            hint = data[1]

        return cls(set(), parent, child, path, first, second, hint)


def reduce_context(hunk):
    """Ignore most of the context."""
    r = _reduce_context
    hfirst = hunk.first
    first = (hfirst[0] + r, hfirst[1] - r * 2)
    hsecond = hunk.first
    second = (hsecond[0] + r, hsecond[1] - r * 2)
    h = hunk
    return Hunk(h.deps, h.parent, h.child, h.path, first, second, h.hint)


def parse_hunks(parent, child=None):
    """Parse hunks in diff."""
    hunks = []
    with diff(parent, child) as proc:
        reader = linereader(proc.stdout)
        try:
            # Find hunks
            path = None
            while True:
                line = next(reader)
                if line.startswith("+++ b/"):
                    _, _, path = line.partition("+++ b/")
                    path = path.strip()
                elif line.startswith("@@ -"):
                    assert path
                    hunk = Hunk.from_line(parent, child, path, line)
                    hunks.append(hunk)
        except StopIteration:
            pass
        return hunks


def parse_blame_line(line):
    rev, _, rest = line.partition(" ")
    number, _, _ = rest.partition(")")
    return rev, int(number)


def find_revs(stream, hunks):
    """Find revisions in blame stream."""
    last = 0
    line = ""
    reader = linereader(stream)
    for hunk in hunks:
        line_number = hunk.first[0]
        lines = line_number - last
        last = line_number
        for _ in range(lines):
            line = next(reader)
        if not line:  # TODO assert we are not skipping because of a an error
            break
        rev, number = parse_blame_line(line)
        assert line_number == number
        hunk_size = hunk.first[1]
        for _ in range(hunk_size):
            line = next(reader)
            rev, number = parse_blame_line(line)
            hunk.deps.add(rev)
        last += hunk_size


def blame_hunks(hunks):
    "Blame changes described by hunks."
    by_parent_path = defaultdict(list)
    for hunk in hunks:
        hunk = reduce_context(hunk)
        by_parent_path[(hunk.parent, hunk.path)].append(hunk)
    for rev_path in by_parent_path.keys():
        with blame(*rev_path) as proc:
            find_revs(proc.stdout, by_parent_path[rev_path])


def get_parents(base):
    """Get the parents of a commit."""
    res = srun(["git", "rev-parse", f"{base}^@"], stdout=PIPE, check=True)
    return [line.strip() for line in res.stdout.splitlines()]


def dig(base):
    """Compare all parents to the base to find all depending commits."""
    if base == "WORKING":
        hunks = parse_hunks("HEAD", base)
    else:
        hunks = []
        for parent in get_parents(base):
            hunks += parse_hunks(parent, base)
    blame_hunks(hunks)
    depends = set()
    for hunk in hunks:
        depends.update(hunk.deps)
    for depend in depends:
        print_depend(depend)


@click.command()
@click.option(
    "-v/-nv",
    "--verbose/--no-verbose",
    default=False,
    help="Verbose output",
)
@click.option(
    "--base",
    "-b",
    default="WORKING",
    help="Base revision for dig (commit-ish) default: WORKING",
)
def main(verbose, base):
    """Find what commits depend on a diff.

    By default it compares your working copy against HEAD. If you provide a base
    revision it will compare that revision against its parents.
    """
    init()
    global _devnull
    global _verbose
    if verbose:
        _devnull = None
        _verbose = True
    dig(base)
