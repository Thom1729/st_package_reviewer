import argparse
import io
import logging
from pathlib import Path
import re
import sys
import tempfile

from github3 import GitHub

from . import set_debug, debug_active
from . import repo_tools
from .runner import CheckRunner
from .check import file as file_c, repo as repo_c


l = logging.getLogger("package_reviewer")


def _prepare_nargs(nargs):
    new_nargs = []
    for arg in nargs:
        if re.match(r"https?://", arg):
            m = re.match(r"^https://github\.com/([^/]+)/([^/]+)$", arg)
            if not m:
                l.error("'%s' is not a valid URL to a github repository. "
                        "At this moment, no other hosters are supported.",
                        arg)
                return None
            new_nargs.append(m.group(1, 2))
        else:
            path = Path(arg)
            if not path.is_dir():
                l.error("'%s' is not a URL or directory", path)
                return None
            new_nargs.append(path)

    return new_nargs


def main():
    """Return values:
        0: No errors
        -1: Invalid command line arguments

    Non-interactive mode:
        1: Package check finished with failures
        2: Repository check finished with failures
        3: Both finished with failures
    """

    parser = argparse.ArgumentParser(prog="python -m {}".format(__package__),
                                     description="Check a Sublime Text package for common errors.")
    parser.add_argument("-i", "--interactive", action='store_true',
                        help="Start interactive mode. '-i' and 'nargs' are exclusive. "
                        "Type 'clip' to copy the last report to the clipboard.")
    parser.add_argument("nargs", nargs='*', metavar="path_or_URL",
                        help="URL to the repository or path to the package to be checked.")
    parser.add_argument("--clip", action='store_true',
                        help="Copy report to clipboard.")
    parser.add_argument("--repo-only", action='store_true',
                        help="Do not check the package itself and only its repository.")
    parser.add_argument("-v", "--verbose", action='store_true',
                        help="Increase verbosity.")
    parser.add_argument("--debug", action='store_true',
                        help="Enter pdb on excpetions. Implies --verbose.")
    args = parser.parse_args()

    # post parsing
    if args.debug:
        args.verbose = True
        set_debug(True)
    if args.nargs and args.interactive:
        print("error: '-i' and 'nargs' are exclusive.")
        parser.print_usage()
        return -1

    # configure logging
    l.addHandler(logging.StreamHandler())
    log_level = logging.DEBUG if args.verbose else logging.INFO
    l.setLevel(log_level)

    # verify args
    nargs = _prepare_nargs(args.nargs)
    if nargs is None:
        return -1

    # start doing work
    gh = GitHub()

    out = io.StringIO()

    def _process_arg(arg, orig_arg):
        exit_code = 0
        if not isinstance(arg, Path):
            repo_location, url = arg, orig_arg
            _report_for(repo_location[1], out)

            l.info("Repository URL: %s", url)
            if not args.repo_only:
                print("### Repository checks ###", file=out)
                print(file=out)

            l.debug("Fetching repository information for %s", repo_location)
            repo = gh.repository(*repo_location)
            l.debug("Github rate limit remaining: %s", repo.ratelimit_remaining)
            if not repo:
                l.error("'%s' does not point to a public repository\n", url)
                return

            if not _run_checks(repo_c.get_checkers(), out, [repo]):
                exit_code = 2
            print(file=out)

            if args.repo_only:
                l.info("Skipping package download due to --repo-only option")
                return exit_code

            ref = repo_tools.latest_ref(repo)
            l.info("Latest ref: %s", ref)

            path = repo_tools.download(repo, ref, tmpdir)
            if path is None:
                l.error("Downloading %s failed; skipping package checks...", url)
                return exit_code

            print("### Package checks ###", file=out)
            print(file=out)

        else:
            path = arg
            _report_for(path.name, out)
            l.info("Package path: %s", path)

        if not _run_checks(file_c.get_checkers(), out, [path]):
            exit_code = 1

        return exit_code

    def _finalize_report():
        print(file=out)
        print("For more details on the report messages (for example how to resolve them), go to:\n"
              "https://github.com/packagecontrol/package_reviewer/wiki", file=out)
        print(file=out)
        report = out.getvalue()
        print(report, end='')

        out.close()
        return report

    with tempfile.TemporaryDirectory(prefix="pkg-rev_") as tmpdir_s:
        tmpdir = Path(tmpdir_s)

        if args.interactive:
            last_report = None
            while True:
                try:
                    orig_arg = input("path/url> ")
                except (EOFError, KeyboardInterrupt):
                    return 0

                if not orig_arg or orig_arg == '\x16':
                    # '\x16' is produced when pressing ctrl+v on windows
                    continue
                elif orig_arg == "clip":
                    if last_report:
                        clip(last_report)
                    else:
                        print("Nothing to copy")
                    continue

                arg = _prepare_nargs([orig_arg])
                if arg is None:
                    continue
                else:
                    _process_arg(arg[0], orig_arg)
                    last_report = _finalize_report()
                    out = io.StringIO()
        else:
            exit_code = 0
            for arg, orig_arg in zip(nargs, args.nargs):
                exit_code &= _process_arg(arg, orig_arg)

            _finalize_report()
            if args.clip:
                clip(report)

            return exit_code

def clip(text):
    import pyperclip
    pyperclip.copy(text)
    print("Report copied to clipboard")


def _report_for(name, file):
    print(file=file)
    print("##", "Report for", name, "#" * (40 - len(name)), file=file)
    print(file=file)


def _run_checks(checkers, file, args=[], kwargs={}):
    runner = CheckRunner(checkers)
    runner.run(*args, **kwargs)
    runner.report(file=file)
    return runner.result()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        if debug_active():
            import pdb
            pdb.post_mortem()
        raise
