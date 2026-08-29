"""Read and act on the comment threads of a project's review pull request.

Run `python3 tools/review/review_threads.py --help`.

A review here is a long-lived pull request against a frozen base branch, so its
threads are the unit of work during a polish pass: read what is open, do the
work, reply saying what was done, resolve. `gh` exposes none of that directly —
threads, their resolution state and the reply mutation are GraphQL only — which
is why this exists rather than a shell alias.

The one subtlety worth knowing before changing anything: GitHub sets a thread's
`line` to null once it goes outdated, which happens as soon as a fix is pushed,
and keeps the position only in `originalLine`. Reading `line` alone therefore
loses exactly the threads that have already been acted on, while continuing to
work perfectly for every untouched one — a failure that looks like success until
someone notices a resolved-in-spirit thread has vanished from the list. The
fallback in `parse_threads` is the whole reason this is a module with tests
rather than a one-line jq filter.
"""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field

THREAD_QUERY = """
query($owner:String!, $repo:String!, $number:Int!) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      reviewThreads(first:100) {
        nodes {
          id isResolved isOutdated path line originalLine
          comments(first:20) { nodes { author { login } body } }
        }
      }
    }
  }
}
"""

REPLY_MUTATION = """
mutation($thread:ID!, $body:String!) {
  addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$thread, body:$body}) {
    comment { author { login } body }
  }
}
"""

RESOLVE_MUTATION = """
mutation($thread:ID!) {
  resolveReviewThread(input:{threadId:$thread}) {
    thread { isResolved }
  }
}
"""


class GraphQLError(RuntimeError):
    """A GraphQL request failed, with the message GitHub actually gave."""


def raise_for_graphql_errors(payload):
    """GraphQL reports failures in the body, not only in the exit status.

    A missing repository comes back as `data.repository: null` plus an `errors`
    list; without this the caller sees a KeyError several frames away from the
    thing that actually went wrong.
    """
    errors = payload.get("errors")
    if errors:
        raise GraphQLError("; ".join(error.get("message", "unknown") for error in errors))
    return payload


@dataclass
class Thread:
    identifier: str
    path: str
    line: int | None
    is_resolved: bool
    is_outdated: bool
    comments: list[tuple[str, str]] = field(default_factory=list)


def parse_threads(payload):
    """Turn a GraphQL response into Thread records.

    `line` is null on outdated threads, so fall back to `originalLine`. Both are
    null on a thread attached to a whole file rather than a line, which is why
    the result is Optional rather than an int.
    """
    nodes = payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    threads = []
    for node in nodes:
        line = node.get("line")
        if line is None:
            line = node.get("originalLine")
        comments = [
            ((comment.get("author") or {}).get("login") or "unknown", comment.get("body") or "")
            for comment in node["comments"]["nodes"]
        ]
        threads.append(Thread(
            identifier=node["id"],
            path=node["path"],
            line=line,
            is_resolved=node["isResolved"],
            is_outdated=node["isOutdated"],
            comments=comments,
        ))
    return threads


def unresolved(threads):
    return [thread for thread in threads if not thread.is_resolved]


def format_threads(threads):
    if not threads:
        return "no open threads"
    lines = []
    for thread in threads:
        location = thread.path if thread.line is None else f"{thread.path}:{thread.line}"
        flags = []
        if thread.is_outdated:
            flags.append("outdated")
        if thread.is_resolved:
            flags.append("resolved")
        suffix = f"  ({', '.join(flags)})" if flags else ""
        lines.append(f"{thread.identifier}  {location}{suffix}")
        for author, body in thread.comments:
            stripped = body.strip()
            first_line = stripped.splitlines()[0] if stripped else ""
            lines.append(f"    {author}: {first_line}")
    return "\n".join(lines)


def run_graphql(query, **variables):
    command = ["gh", "api", "graphql", "-f", f"query={query}"]
    for name, value in variables.items():
        flag = "-F" if isinstance(value, int) else "-f"
        command += [flag, f"{name}={value}"]
    completed = subprocess.run(command, capture_output=True, text=True)
    if not completed.stdout.strip():
        raise GraphQLError(completed.stderr.strip() or f"gh exited {completed.returncode}")
    return raise_for_graphql_errors(json.loads(completed.stdout))


def current_pull_request():
    """Resolve owner, repo and PR number for the checked-out branch."""
    completed = subprocess.run(
        ["gh", "pr", "view", "--json", "number,headRepository,headRepositoryOwner"],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(completed.stdout)
    return (
        data["headRepositoryOwner"]["login"],
        data["headRepository"]["name"],
        data["number"],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subcommands = parser.add_subparsers(dest="command", required=True)

    listing = subcommands.add_parser("list", help="show threads on the current branch's PR")
    listing.add_argument("--all", action="store_true", help="include resolved threads")

    replying = subcommands.add_parser("reply", help="reply within a thread")
    replying.add_argument("thread")
    replying.add_argument("body")

    resolving = subcommands.add_parser("resolve", help="mark a thread resolved")
    resolving.add_argument("thread")

    arguments = parser.parse_args(argv)

    if arguments.command == "list":
        owner, repo, number = current_pull_request()
        threads = parse_threads(run_graphql(THREAD_QUERY, owner=owner, repo=repo, number=number))
        if not arguments.all:
            threads = unresolved(threads)
        print(format_threads(threads))
    elif arguments.command == "reply":
        run_graphql(REPLY_MUTATION, thread=arguments.thread, body=arguments.body)
        print("replied")
    elif arguments.command == "resolve":
        run_graphql(RESOLVE_MUTATION, thread=arguments.thread)
        print("resolved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
