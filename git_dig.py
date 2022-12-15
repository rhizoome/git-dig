"""Show dependencies of a commit."""

import os
import sys
from collections import defaultdict
from contextlib import contextmanager
from subprocess import DEVNULL, PIPE, Popen, run

import click
from colorama import Fore, init

_devnull = DEVNULL
_verbose = False


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
def show(rev=None):
    """Get git rev."""
    if rev is None:
        rev = []
    else:
        rev = [rev]
    with SPopen(["git", "show"] + rev, stdout=PIPE) as proc:
        yield proc
        assert proc.wait() == 0


@contextmanager
def blame(rev, path):
    """Blame git rev/path."""
    with SPopen(
        ["git", "blame", "-s", rev, "--", path],
        stdout=PIPE,
    ) as proc:
        yield proc
        assert proc.wait() == 0


def current_revision():
    return srun(
        ["git", "rev-parse", "HEAD"],
        stdout=PIPE,
        check=True,
    ).stdout.strip()


def print_depend(rev):
    srun(["git", "show", "--quiet", "--oneline"] + [rev], stdout=sys.stdout, check=True)


def linereader(stream):
    while True:
        line = stream.readline()
        if not line:
            return
        line = line.strip("\n")
        yield line


class Hunk:
    __slots__ = ("deps", "rev", "path", "first", "second", "hint")

    def __init__(self, rev, path, line):
        self.deps = set()
        self.rev = rev
        self.path = path
        data = [data.strip() for data in line.split("@@") if data]
        first, _, second = data[0].partition(" ")
        self.first = tuple([int(i) for i in first[1:].split(",")])
        self.second = tuple([int(i) for i in second[1:].split(",")])
        self.hint = ""
        if len(data) > 1:
            self.hint = data[1]

    def __repr__(self):
        return f'path: "{self.path}" first: {self.first} second: {self.second}'


def parse_hunks(rev=None):
    """Parse hunks in diff."""
    hunks = []
    with show(rev) as proc:
        reader = linereader(proc.stdout)
        try:
            # Forward to message
            while True:
                line = next(reader)
                if not line:
                    break
            # Forward to diff
            while True:
                line = next(reader)
                if not line:
                    break
            # Find hunks
            path = None
            while True:
                line = next(reader)
                if line.startswith("+++ b/"):
                    _, _, path = line.partition("+++ b/")
                    path = path.strip()
                elif line.startswith("@@ -"):
                    if not path:
                        __import__("pdb").set_trace()
                        pass
                    hunk = Hunk(rev, path, line)
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
        hunk.deps.add(rev)
        hunk_size = hunk.first[1]
        for _ in range(hunk_size):
            line = next(reader)
            rev, number = parse_blame_line(line)
            hunk.deps.add(rev)
        last += hunk_size


def blame_hunks(hunks):
    "Blame changes described by hunks."
    by_rev_path = defaultdict(list)
    for hunk in hunks:
        by_rev_path[(hunk.rev, hunk.path)].append(hunk)
    for rev_path in by_rev_path.keys():
        with blame(*rev_path) as proc:
            find_revs(proc.stdout, by_rev_path[rev_path])


@click.command()
@click.option(
    "-v/-nv",
    "--verbose/--no-verbose",
    default=False,
    help="Verbose output",
)
def main(verbose):
    """Click entrypoint."""
    init()
    global _devnull
    global _verbose
    if verbose:
        _devnull = None
        _verbose = True
    rev = current_revision()
    hunks = parse_hunks(rev)
    blame_hunks(hunks)
    depends = set()
    for hunk in hunks:
        depends.update(hunk.deps)
    for depend in depends:
        if not rev.startswith(depend):
            print_depend(depend)
