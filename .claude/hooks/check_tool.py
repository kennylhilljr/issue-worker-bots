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
            "timeout", "gtimeout")
# Wrapper options that take the next word as their value (so it is not the program).
WRAPPER_VALUE_OPTIONS = {
    "env": ("-u", "-C", "-P", "-S"),
    "sudo": ("-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T"),
    "nice": ("-n",),
    "caffeinate": ("-t", "-w"),
    "exec": ("-a",),
    "xargs": ("-I", "-J", "-L", "-n", "-P", "-s", "-E", "-d", "-a", "-R", "-S"),
    "timeout": ("-s", "-k", "--signal", "--kill-after"),
    "gtimeout": ("-s", "-k", "--signal", "--kill-after"),
}
SHELLS = ("sh", "bash", "zsh", "dash")
IWB_DRY_RUN_COMMANDS = ("intake", "analyze", "approve", "code")
LAUNCHCTL_CHANGES = ("load", "unload", "bootstrap", "bootout", "kickstart", "enable")
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
LAUNCHCTL_MESSAGE = ("Blocked: launchctl {sub} changes launchd jobs; "
                     "only launchctl list and print are allowed from an agent session.")
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

    Returns a list of (words, bodies, terminator) where terminator is the separator
    that ended the command ('' at the end of input).
    """
    commands = []
    words, bodies = [], []
    ends = []
    pending_target = False
    for kind, value, body, start, end in _tokens(command):
        if kind == "word":
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
    return [entry for entry in commands if entry[0]]


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
    """For sh/bash/zsh/dash args, return (has_c, index of the first operand)."""
    has_c, index = False, 0
    while index < len(args):
        arg = args[index]
        if arg in ("-o", "+o", "-O", "+O"):
            index += 2
            continue
        if arg == "--":
            return has_c, index + 1
        if arg.startswith("--"):
            index += 1
            continue
        if arg[:1] in ("-", "+") and len(arg) > 1:
            if arg[0] == "-" and "c" in arg[1:]:
                has_c = True
            index += 1
            continue
        break
    return has_c, index


def check_simple(words, bodies, terminator, depth):
    """Return a block message for one simple command, or None."""
    index, wrapper, via_xargs = 0, None, False
    while index < len(words):
        word = words[index]
        base = _basename(word)
        if _is_assignment(word):
            index += 1
        elif base in WRAPPERS:
            wrapper = base
            via_xargs = via_xargs or base == "xargs"
            index += 1
            if base in ("timeout", "gtimeout"):
                while index < len(words) and words[index].startswith("-"):
                    index += 2 if words[index] in WRAPPER_VALUE_OPTIONS[base] else 1
                index += 1  # the duration
        elif word.startswith("-") and len(word) > 1:
            index += 2 if word in WRAPPER_VALUE_OPTIONS.get(wrapper, ()) else 1
        else:
            break
    words, bodies = words[index:], bodies[index:]
    if not words:
        return None
    program, args, arg_bodies = _basename(words[0]), words[1:], bodies[1:]

    if program in SHELLS:
        has_c, operand = _shell_command_string(args)
        if has_c:
            if operand >= len(args):
                return None
            return check_command(args[operand], depth + 1)
        if operand >= len(args):
            return None
        program, args, arg_bodies = _basename(args[operand]), args[operand + 1:], arg_bodies[operand + 1:]
    elif program == "eval":
        return check_command(" ".join(args), depth + 1)
    elif _is_python(program):
        operand = 0
        while operand < len(args):
            arg = args[operand]
            if arg in ("-c", "-m") or (arg.startswith("-") and not arg.startswith("--")
                                       and ("c" in arg[1:] or "m" in arg[1:])):
                return None     # inline code or a module, not a script path
            if arg in ("-W", "-X"):
                operand += 2
            elif arg.startswith("-") and len(arg) > 1:
                operand += 1
            else:
                break
        if operand >= len(args):
            return None
        program, args, arg_bodies = _basename(args[operand]), args[operand + 1:], arg_bodies[operand + 1:]
    elif program in (".", "source"):
        if not args:
            return None
        program, args, arg_bodies = _basename(args[0]), args[1:], arg_bodies[1:]

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
    if program == "install-intake.sh":
        return INSTALL_MESSAGE
    if program == "launchctl":
        subcommands = [arg for arg in args if not arg.startswith("-")]
        if subcommands and subcommands[0] in LAUNCHCTL_CHANGES:
            return LAUNCHCTL_MESSAGE.format(sub=subcommands[0])
        if via_xargs and not subcommands:
            return LAUNCHCTL_MESSAGE.format(sub="run through xargs")
    return None


def check_command(command, depth=0):
    """Return a block message for a Bash command string, or None to allow it."""
    if depth > MAX_DEPTH:
        return PARSE_MESSAGE.format(reason="shells nested too deeply")
    try:
        commands = split_commands(command)
    except Unparsable as error:
        return PARSE_MESSAGE.format(reason=error)
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
