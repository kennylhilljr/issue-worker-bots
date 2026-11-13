#!/usr/bin/env python3
"""Claude Code tool hook for this checkout.

--mode pre blocks Write/Edit/MultiEdit calls on secret-shaped files, and Bash
commands that would run the live pipeline (iwb), install-intake.sh or change
launchd jobs. --mode post currently allows everything.
"""
import argparse
import io
import json
import os
import shlex
import sys
from pathlib import PurePosixPath

EDIT_TOOLS = ("Write", "Edit", "MultiEdit")

PUNCTUATION = ";&|<>()`\n"
BLANKS = " \t\r"
MAX_DEPTH = 3
# bash operators, longest first so a run of punctuation splits greedily.
REDIRECTIONS = ("&>>", "<<<", "&>", ">>", ">&", "<&", "<>", "<<", ">|", ">", "<")
SEPARATORS = ("&&", "||", ";;", "|&", ";", "&", "|", "(", ")", "`", "\n")
OPERATORS = sorted(REDIRECTIONS + SEPARATORS, key=len, reverse=True)

WRAPPERS = ("env", "sudo", "nohup", "time", "command", "exec", "nice", "caffeinate", "xargs",
            "timeout", "gtimeout", "watch", "arch", "setsid", "stdbuf", "taskpolicy", "unbuffer",
            "builtin", "script")
# Shell keywords that can start a simple command once ; and newlines have split it.
RESERVED_WORDS = ("{", "}", "!", "if", "then", "elif", "else", "fi", "do", "done", "while", "until",
                  "case", "esac", "coproc")
# Programs that only compare words; a quoted "$x" or [ there never names iwb.
TEST_PROGRAMS = ("[", "[[", "test")
# Wrapper options that take the next word as their value (so it is not the program).
WRAPPER_VALUE_OPTIONS = {
    "env": ("-u", "-C", "-P", "--unset", "--chdir"),
    "sudo": ("-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "--user", "--group",
             "--host", "--prompt", "--chdir", "--close-from", "--role", "--type", "--other-user"),
    "time": ("-o", "-f", "--output", "--format"),
    "stdbuf": ("-i", "-o", "-e", "--input", "--output", "--error"),
    "taskpolicy": ("-c", "-t", "-l", "-p", "-g"),
    "script": ("-t", "-T"),
    "nice": ("-n", "--adjustment"),
    "caffeinate": ("-t", "-w"),
    "exec": ("-a",),
    "xargs": ("-I", "-J", "-L", "-n", "-P", "-s", "-E", "-d", "-a", "-R", "-S"),
    "timeout": ("-s", "-k", "--signal", "--kill-after"),
    "gtimeout": ("-s", "-k", "--signal", "--kill-after"),
    "watch": ("-n", "--interval", "-q", "--equexit"),
    "arch": ("-e",),
}
SHELLS = ("sh", "bash", "zsh", "dash", "ksh", "mksh", "fish")
IWB_DRY_RUN_COMMANDS = ("intake", "analyze", "approve", "code")
# launchctl subcommands that only read; every other one (load, bootout, kickstart, submit, ...) is blocked.
LAUNCHCTL_READ_ONLY = ("list", "print", "print-cache", "print-disabled", "blame", "dumpstate",
                       "managerpid", "manageruid", "managername", "error", "variant", "version",
                       "help", "getenv", "hostinfo")
IWB_SUBCOMMANDS = ("tick", "intake", "analyze", "approve", "code")
# python -m modules whose first operand is a script they run.
SCRIPT_RUNNING_MODULES = ("cProfile", "profile", "pdb", "trace")
GUARDED_PROGRAMS = ("iwb", "install-intake.sh", "launchctl")
EXPANSION_CHARS = "$`{*?["

IWB_MESSAGE = ("Blocked: iwb {sub} runs the live pipeline from this checkout (launchd owns it). "
               "Use --dry-run on intake/analyze/approve/code (never with --from-queue or --job); "
               "iwb tick has no dry run.")
IWB_DYNAMIC_MESSAGE = ("Blocked: iwb arguments that use shell expansion or command substitution "
                       "cannot be checked; iwb runs the live pipeline from this checkout. "
                       "Spell out the arguments.")
IWB_XARGS_MESSAGE = ("Blocked: iwb run through xargs takes its arguments from stdin, so it "
                     "cannot be checked; iwb runs the live pipeline from this checkout.")
INSTALL_MESSAGE = ("Blocked: install-intake.sh installs the live launchd job; "
                   "do not run it from an agent session.")
LAUNCHCTL_MESSAGE = ("Blocked: launchctl {sub} can change launchd jobs; "
                     "only read-only launchctl subcommands such as list and print are allowed.")
SHELL_STDIN_MESSAGE = ("Blocked: a shell reading its commands from stdin or a file descriptor "
                       "cannot be checked (iwb, install-intake.sh, launchctl rules); "
                       "pass the command with -c instead.")
PARSE_MESSAGE = "Blocked: this command could not be parsed ({reason}), so it is not allowed."


class Unparsable(Exception):
    """The command cannot be split reliably; the hook fails closed."""


def is_secret_path(file_path):
    """Return True when file_path looks like a secret file, mirroring iwb's ALWAYS_BLOCKED secrets."""
    parts = PurePosixPath(file_path).parts
    if not parts:
        return False
    name = parts[-1]
    if name.startswith(".env") or name.endswith(".pem") or name.endswith(".key"):
        return True
    return "secrets" in parts[:-1]


class _CommentStream(io.StringIO):
    """StringIO that records each comment shlex skips: (start of '#', end, ended by newline)."""

    def __init__(self, text):
        super().__init__(text)
        self.comments = []

    def readline(self, *args):
        start = self.tell() - 1     # shlex has just read the '#'
        line = super().readline(*args)
        self.comments.append((start, self.tell(), line.endswith("\n")))
        return line


def _skip_blanks(text, pos, end):
    while pos < end and text[pos] in BLANKS:
        pos += 1
    return pos


def _split_operator(run):
    """Split a run of punctuation into bash operators, longest match first."""
    pieces, pos = [], 0
    while pos < len(run):
        for op in OPERATORS:
            if run.startswith(op, pos):
                pieces.append(op)
                pos += len(op)
                break
        else:   # unreachable: every punctuation character is an operator
            pieces.append(run[pos])
            pos += 1
    return pieces


def _tokens(command):
    """Yield ('word', value, body, start, end) and ('op', value, None, start, end) tokens."""
    stream = _CommentStream(command)
    lexer = shlex.shlex(stream, posix=True, punctuation_chars=PUNCTUATION)
    lexer.whitespace = BLANKS
    lexer.whitespace_split = True
    lexer.commenters = "#"
    while True:
        start = stream.tell() - len(lexer._pushback_chars)
        seen = len(stream.comments)
        try:
            token = lexer.get_token()
        except ValueError as error:
            raise Unparsable(str(error)) from None
        end = stream.tell() - len(lexer._pushback_chars)
        comments = stream.comments[seen:]
        # Comments before the token: each one ends at its newline, which separates commands.
        pos = start
        while comments:
            pos = _skip_blanks(command, pos, end)
            comment_start, comment_end, newline = comments[0]
            if pos != comment_start:
                break
            if newline:
                yield ("op", "\n", None, comment_end - 1, comment_end)
            pos = comment_end
            comments = comments[1:]
        if token is None:
            return
        body_end = comments[0][0] if comments else end
        body_start = _skip_blanks(command, pos, body_end)
        body = command[body_start:body_end].rstrip(BLANKS)
        if body == token and all(ch in PUNCTUATION for ch in token):
            yield ("op", token, None, body_start, body_start + len(body))
            if comments and comments[0][2]:
                yield ("op", "\n", None, comments[0][1] - 1, comments[0][1])
            continue
        if comments:
            # shlex ends a word at '#', the shell does not; the rest of the line is unknown.
            raise Unparsable("'#' inside a word; quote it")
        if body == "\\\n":
            continue    # a bare line continuation
        if "\\\n" in body:
            token = token.replace("\\\n", "").replace("\n", "")
        yield ("word", token, body, body_start, body_start + len(body))


def split_commands(command):
    """Split a shell command into simple commands.

    Returns (commands, all_bodies): commands is a list of (words, bodies, terminator)
    where terminator is the separator that ended the command ('' at the end of input);
    all_bodies is the source of every word, redirection targets included.
    """
    commands, all_bodies = [], []
    words, bodies = [], []
    ends = []
    pending_target = False
    for kind, value, body, start, end in _tokens(command):
        if kind == "word":
            all_bodies.append(body)
            if pending_target:
                pending_target = False      # a redirection target is not an argument
                continue
            words.append(value)
            bodies.append(body)
            ends.append(end)
            continue
        for index, piece in enumerate(_split_operator(value)):
            if piece in REDIRECTIONS:
                if index == 0 and words and ends[-1] == start and words[-1].isdigit() \
                        and bodies[-1] == words[-1]:
                    words.pop()     # file descriptor, as in 2>&1
                    bodies.pop()
                    ends.pop()
                pending_target = True
                continue
            pending_target = False
            commands.append((words, bodies, piece))
            words, bodies, ends = [], [], []
    commands.append((words, bodies, ""))
    return [entry for entry in commands if entry[0]], all_bodies


def _substitutions(body):
    """Return the text of each $(...) and `...` inside a word's source (quoted ones included)."""
    found, index = [], 0
    while index < len(body):
        if body.startswith("$(", index):
            depth, stop = 0, index + 1
            while stop < len(body):
                if body[stop] == "(":
                    depth += 1
                elif body[stop] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                stop += 1
            found.append(body[index + 2:stop])
            index = stop + 1
        elif body[index] == "`":
            stop = body.find("`", index + 1)
            stop = len(body) if stop < 0 else stop
            found.append(body[index + 1:stop])
            index = stop + 1
        elif body[index] == "\\":
            index += 2
        else:
            index += 1
    return found


def _basename(word):
    return os.path.basename(word.rstrip("/")) if word else ""


def _is_assignment(word):
    name, equals, _ = word.partition("=")
    return bool(equals) and name.isidentifier()


def _is_python(base):
    if base in ("python", "python3"):
        return True
    return base.startswith("python3.") and base[len("python3."):].isdigit()


def _shell_command_string(args):
    """For shell args, return (has_c, reads_stdin, index of the first operand)."""
    has_c, reads_stdin, index = False, False, 0
    while index < len(args):
        arg = args[index]
        if arg in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            index += 2
            continue
        if arg == "--":
            return has_c, reads_stdin, index + 1
        if arg.startswith("--"):
            index += 1
            continue
        if arg[:1] in ("-", "+") and len(arg) > 1:
            if arg[0] == "-" and "c" in arg[1:]:
                has_c = True
            if arg[0] == "-" and "s" in arg[1:]:
                reads_stdin = True
            # -euo pipefail: a cluster ending in o/O takes the next word as its value
            index += 2 if arg[-1] in "oO" else 1
            continue
        break
    return has_c, reads_stdin, index


def _python_operand(args):
    """Candidate script indexes for python args: [] for -c, other -m modules and stdin."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return [index + 1] if index + 1 < len(args) else []
        if arg.startswith("--"):
            index += 2 if arg == "--check-hash-based-pycs" else 1
            continue
        if arg.startswith("-") and len(arg) > 1:
            takes_value = False
            for position, flag in enumerate(arg[1:], start=1):
                if flag == "m":
                    module = arg[position + 1:] or (args[index + 1] if index + 1 < len(args) else "")
                    if module not in SCRIPT_RUNNING_MODULES:
                        return []       # a module, not a script path
                    # Their own options vary (and argparse accepts abbreviations), so every
                    # later word that names a guarded program or may expand to one is a
                    # candidate script, and each candidate is checked.
                    rest = index + (1 if arg[position + 1:] else 2)
                    return [candidate for candidate in range(rest, len(args))
                            if _basename(args[candidate]) in GUARDED_PROGRAMS
                            or any(ch in args[candidate] for ch in EXPANSION_CHARS)]
                if flag == "c":
                    return []       # inline code
                if flag in "WX":
                    takes_value = position == len(arg) - 1
                    break
            index += 2 if takes_value else 1
            continue
        return [index]
    return []


def _check_program(program_word, program_body, args, arg_bodies, via_xargs, terminator, depth):
    """Apply the iwb, install-intake.sh, launchctl and launcher rules to one program."""
    program = _basename(program_word)
    if program == "find":
        for index, arg in enumerate(args):
            if arg in ("-exec", "-execdir", "-ok", "-okdir"):
                stop = index + 1
                while stop < len(args) and args[stop] not in (";", "+"):
                    stop += 1
                message = check_simple(args[index + 1:stop], arg_bodies[index + 1:stop], "", depth + 1)
                if message:
                    return message
        return None
    if program == "iwb":
        if via_xargs:
            return IWB_XARGS_MESSAGE
        if terminator in ("(", "`") or any(ch in body for body in arg_bodies for ch in EXPANSION_CHARS):
            return IWB_DYNAMIC_MESSAGE
        if not args or args[0] in ("-h", "--help"):
            return None
        sub = args[0]
        if sub == "tick":
            return IWB_MESSAGE.format(sub="tick")
        if sub in IWB_DRY_RUN_COMMANDS:
            if "--dry-run" in args and "--from-queue" not in args and "--job" not in args:
                return None
            return IWB_MESSAGE.format(sub=sub)
        return None
    if program not in TEST_PROGRAMS and any(ch in program_body for ch in EXPANSION_CHARS) and \
            any(arg in IWB_SUBCOMMANDS for arg in args):
        return IWB_DYNAMIC_MESSAGE   # $cmd tick, ./i*b tick: the program may be iwb
    if program == "install-intake.sh":
        return INSTALL_MESSAGE
    if program == "launchctl":
        subcommands = [arg for arg in args if not arg.startswith("-")]
        if subcommands and subcommands[0] not in LAUNCHCTL_READ_ONLY:
            return LAUNCHCTL_MESSAGE.format(sub=subcommands[0])
        if not subcommands:     # commands from stdin or xargs
            return LAUNCHCTL_MESSAGE.format(sub="without a subcommand")
    return None


def check_simple(words, bodies, terminator, depth):
    """Return a block message for one simple command, or None."""
    if depth > MAX_DEPTH:
        return PARSE_MESSAGE.format(reason="commands nested too deeply")
    index, wrapper, via_xargs = 0, None, False
    while index < len(words):
        word = words[index]
        base = _basename(word)
        if word in RESERVED_WORDS:
            index += 1
        elif word == "function":
            index += 2      # function NAME { ... }
        elif _is_assignment(word):
            index += 1
        elif base in WRAPPERS:
            wrapper = base
            via_xargs = via_xargs or base == "xargs"
            index += 1
            if base in ("timeout", "gtimeout", "script"):
                while index < len(words) and words[index].startswith("-"):
                    index += 2 if words[index] in WRAPPER_VALUE_OPTIONS[base] else 1
                index += 1  # timeout's duration, script's output file
            elif base == "watch":
                while index < len(words) and words[index].startswith("-"):
                    index += 2 if words[index] in WRAPPER_VALUE_OPTIONS[base] else 1
                return check_command(" ".join(words[index:]), depth + 1)  # watch runs sh -c
        elif wrapper == "env" and word == "-":
            index += 1      # env - is the old spelling of env -i
        elif wrapper == "env" and not word.startswith("--") and word.startswith("-") and "S" in word:
            # env -iS '...': the S flag's value is the rest of the word or the next word
            value = word[word.index("S") + 1:]
            rest = ([value] if value else []) + words[index + 1:]
            return check_command(" ".join(rest), depth + 1)
        elif wrapper == "env" and (word.startswith("-S") or word.startswith("--split-string")):
            # env -S runs its value as a command line, followed by the remaining words.
            if word in ("-S", "--split-string"):
                rest = words[index + 1:]
            else:
                rest = [word.split("=", 1)[1] if word.startswith("--") else word[2:]] + words[index + 1:]
            return check_command(" ".join(rest), depth + 1)
        elif word.startswith("-") and len(word) > 1:
            values = WRAPPER_VALUE_OPTIONS.get(wrapper, ())
            # -u kenny, and a cluster ending in a value flag such as sudo -nu kenny
            takes_value = word in values or (
                not word.startswith("--") and len(word) > 2 and "-" + word[-1] in values)
            index += 2 if takes_value else 1
        else:
            break
    words, bodies = words[index:], bodies[index:]
    if not words:
        return None
    program, program_body = _basename(words[0]), bodies[0]
    args, arg_bodies = words[1:], bodies[1:]

    if program in SHELLS:
        has_c, reads_stdin, operand = _shell_command_string(args)
        if has_c:
            if operand >= len(args):
                return None
            return check_command(args[operand], depth + 1)
        if reads_stdin or operand >= len(args) or args[operand] == "-":
            return SHELL_STDIN_MESSAGE
        program_word, program_body = args[operand], arg_bodies[operand]
        args, arg_bodies = args[operand + 1:], arg_bodies[operand + 1:]
    elif program == "eval":
        return check_command(" ".join(args), depth + 1)
    elif _is_python(program):
        for operand in _python_operand(args):
            message = _check_program(args[operand], arg_bodies[operand], args[operand + 1:],
                                     arg_bodies[operand + 1:], via_xargs, terminator, depth)
            if message:
                return message
        return None
    elif program in (".", "source"):
        if not args:
            return SHELL_STDIN_MESSAGE
        program_word, program_body = args[0], arg_bodies[0]
        args, arg_bodies = args[1:], arg_bodies[1:]
    else:
        program_word = words[0]
    return _check_program(program_word, program_body, args, arg_bodies, via_xargs, terminator, depth)


def check_command(command, depth=0):
    """Return a block message for a Bash command string, or None to allow it."""
    if depth > MAX_DEPTH:
        return PARSE_MESSAGE.format(reason="shells nested too deeply")
    try:
        commands, all_bodies = split_commands(command)
    except Unparsable as error:
        return PARSE_MESSAGE.format(reason=error)
    for body in all_bodies:
        for inner in _substitutions(body):   # "$(./iwb tick)" runs even inside quotes
            message = check_command(inner, depth + 1)
            if message:
                return message
    for words, bodies, terminator in commands:
        message = check_simple(words, bodies, terminator, depth)
        if message:
            return message
    return None


def check_pre(payload):
    """Return (exit_code, message) for a PreToolUse payload."""
    if not isinstance(payload, dict):
        return 0, ""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0, ""
    if tool in EDIT_TOOLS:
        file_path = tool_input.get("file_path")
        if isinstance(file_path, str) and file_path and is_secret_path(file_path):
            return 2, (f"Blocked: {file_path} looks like a secret file. "
                       "Secrets stay in Keychain and reach the bots through prbot.")
        return 0, ""
    if tool == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            message = check_command(command)
            if message:
                return 2, message
    return 0, ""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Claude Code tool hook")
    parser.add_argument("--mode", required=True, choices=["pre", "post"])
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if args.mode == "pre":
        code, message = check_pre(payload)
        if message:
            print(message, file=sys.stderr)
        return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
