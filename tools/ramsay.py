#!/usr/bin/env python2
import argparse
import ast
import imp
import jinja2
import json
import logging
import operator
import os
import re
import subprocess
import sys

"""
Ramsay is the Bazel BUILD file generator for python.
"""

# Source: https://docs.python.org/2/py-modindex.html
SYSTEM_MODULES = [
    "__builtin__", "__future__", "__main__", "_winreg", "abc", "aepack", "aetools", "aetypes", "aifc", "al", "AL",
    "anydbm", "applesingle", "argparse", "array", "ast", "asynchat", "asyncore", "atexit", "audioop", "autoGIL",
    "base64", "BaseHTTPServer", "Bastion", "bdb", "binascii", "binhex", "bisect", "bsddb", "buildtools", "bz2",
    "calendar", "Carbon", "cd", "cfmfile", "cgi", "CGIHTTPServer", "cgitb", "chunk", "cmath", "cmd", "code", "codecs",
    "codeop", "collections", "ColorPicker", "colorsys", "commands", "compileall", "compiler", "ConfigParser",
    "contextlib", "Cookie", "cookielib", "copy", "copy_reg", "cPickle", "cProfile", "crypt", "cStringIO", "csv",
    "ctypes", "curses", "datetime", "dbhash", "dbm", "decimal", "DEVICE", "difflib", "dircache", "dis", "distutils",
    "dl", "doctest", "DocXMLRPCServer", "dumbdbm", "dummy_thread", "dummy_threading", "EasyDialogs", "email",
    "encodings", "ensurepip", "errno", "exceptions", "fcntl", "filecmp", "fileinput", "findertools", "fl", "FL", "flp",
    "fm", "fnmatch", "formatter", "fpectl", "fpformat", "fractions", "FrameWork", "ftplib", "functools",
    "future_builtins", "gc", "gdbm", "gensuitemodule", "getopt", "getpass", "gettext", "gl", "GL", "glob", "grp",
    "gzip", "hashlib", "heapq", "hmac", "hotshot", "htmlentitydefs", "htmllib", "HTMLParser", "httplib", "ic", "icopen",
    "imageop", "imaplib", "imgfile", "imghdr", "imp", "importlib", "imputil", "inspect", "io", "itertools", "jpeg",
    "json", "keyword", "lib2to3", "linecache", "locale", "logging", "macerrors", "MacOS", "macostools", "macpath",
    "macresource", "mailbox", "mailcap", "marshal", "math", "md5", "mhlib", "mimetools", "mimetypes", "MimeWriter",
    "mimify", "MiniAEFrame", "mmap", "modulefinder", "msilib", "msvcrt", "multifile", "multiprocessing", "mutex", "Nav",
    "netrc", "new", "nis", "nntplib", "numbers", "operator", "optparse", "os", "ossaudiodev", "parser", "pdb", "pickle",
    "pickletools", "pipes", "PixMapWrapper", "pkgutil", "platform", "plistlib", "popen2", "poplib", "posix",
    "posixfile", "pprint", "profile", "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc", "Queue", "quopri",
    "random", "re", "readline", "resource", "rexec", "rfc822", "rlcompleter", "robotparser", "runpy", "sched",
    "ScrolledText", "select", "sets", "sgmllib", "sha", "shelve", "shlex", "shutil", "signal", "SimpleHTTPServer",
    "SimpleXMLRPCServer", "site", "smtpd", "smtplib", "sndhdr", "socket", "SocketServer", "spwd", "sqlite3", "ssl",
    "stat", "statvfs", "string", "StringIO", "stringprep", "struct", "subprocess", "sunau", "sunaudiodev",
    "SUNAUDIODEV", "symbol", "symtable", "sys", "sysconfig", "syslog", "tabnanny", "tarfile", "telnetlib", "tempfile",
    "termios", "test", "textwrap", "thread", "threading", "time", "timeit", "Tix", "Tkinter", "token", "tokenize",
    "trace", "traceback", "ttk", "tty", "turtle", "types", "unicodedata", "unittest", "urllib", "urllib2", "urlparse",
    "user", "UserDict", "UserList", "UserString", "uu", "uuid", "videoreader", "W", "warnings", "wave", "weakref",
    "webbrowser", "whichdb", "winsound", "wsgiref", "xdrlib", "xml", "xmlrpclib", "zipfile", "zipimport", "zlib"]


def main(argv):
    # type: (list) -> None
    args = parse_args(argv)
    init_logging(args.enable_debug)
    config = Config.from_args(args)
    workspace = Workspace.from_config(config)
    ramsay = Ramsay(workspace, config.ignored_files, config.ignored_test_files, config.manual_imports,
            config.manual_dependencies, config.manual_data_dependencies, config.pattern_deps, config.post_sections,
            config.allow_scoped_imports, config.generate_library_targets, config.generate_test_targets,
            config.generate_shared_library)
    build_file_contents = ramsay.files(args.files)
    try:
        imp.find_module("yapf")
        from yapf.yapflib.yapf_api import FormatCode
        build_file_contents, changed = FormatCode(build_file_contents)  # defaults to pep8
    except ImportError:
        pass
    print(build_file_contents)


def parse_args(argv):
    # type: (list) -> argparse.Namespace
    parser = argparse.ArgumentParser(
        description="Generates BUILD files for a Python source tree.")
    parser.add_argument("files",
                        metavar="FILE",
                        type=str,
                        nargs="+",
                        help="sets the Python source files to process")
    parser.add_argument("--allow-scoped-imports",
                        dest="allow_scoped_imports",
                        action="store_true",
                        default=False,
                        help="allows non-top level imports")
    parser.add_argument("--debug",
                        dest="enable_debug",
                        action="store_true",
                        default=False,
                        help="logs debug information to stderr")
    parser.add_argument("--workspace-dir",
                        metavar="WORKSPACE",
                        dest="workspace_dir",
                        type=str,
                        default=Workspace.find_workspace_abs_dirpath(),
                        help="overrides the automically-discovered Bazel workspace directory")
    return parser.parse_args(argv[1:])


def init_logging(enable_debug):
    # type: (bool) -> None
    logging.basicConfig(
        format="%(asctime)-15s: %(name)s: %(message)s",
        stream=sys.stderr,
        level=logging.DEBUG if enable_debug else logging.ERROR)


class Ramsay:
    RULESET = "@com_bluecore_rules_pyz//rules_python_zip:rules_python_zip.bzl"
    LIBRARY_TARGET = "pyz_library"
    TEST_TARGET = "pyz_test"
    TEST_PREFIX = "test_"

    _logger = logging.getLogger(__name__)

    def __init__(self, workspace, ignored_files, ignored_test_files, manual_imports, manual_dependencies,
            manual_data_dependencies, pattern_deps, post_sections, allow_scoped_imports, generate_library_targets,
            generate_test_targets, generate_shared_library):
        self.workspace = workspace
        self.ignored_files = ignored_files
        self.ignored_test_files = ignored_test_files
        self.manual_imports = manual_imports
        self.manual_dependencies = manual_dependencies
        self.manual_data_dependencies = manual_data_dependencies
        self.pattern_deps = pattern_deps
        self.post_sections = post_sections
        self.allow_scoped_imports = allow_scoped_imports
        self.generate_library_targets = generate_library_targets
        self.generate_test_targets = generate_test_targets
        self.generate_shared_library = generate_shared_library

    def files(self, filepaths):
        # type: (list) -> str

        # read all the code ahead of processing so we don't fail during transforming on files that we can't open.
        filepaths = self._filter_ignored_files(filepaths)
        codes = self._parse_code_files(filepaths)

        # we only care about import nodes.
        import_nodes = self._filter_import_nodes(codes)
        import_stmts = self._reify_import_nodes(import_nodes)
        assert(len(import_nodes.keys()) == len(import_stmts.keys()))
        assert(len(import_nodes.values()) == len(import_stmts.values()))

        # since we can't correctly evaluate dynamic imports, we allow users to synthesize imports and dependencies
        # via .ramsayrc files.
        imports_sourcemap = self._resolve_import_stmts(import_stmts)
        imports_sourcemap = self._synthesize_imports(imports_sourcemap)
        imports_sourcemap = self._synthesize_dependencies(imports_sourcemap)
        imports_sourcemap = self._apply_pattern_deps(imports_sourcemap)

        build_template = BazelBuildTemplate()
        if self.generate_library_targets:
            self._build_library_targets(imports_sourcemap, build_template)
        if self.generate_test_targets:
            self._build_test_targets(imports_sourcemap, build_template)
        if self.generate_shared_library:
            self._build_shared_library_target(imports_sourcemap, build_template)
        self._append_post_sections(build_template)
        return str(build_template)

    def _filter_ignored_files(self, filepaths):
        return [filepath for filepath in filepaths if filepath not in self.ignored_files]

    def _parse_code_files(self, filepaths):
        # type: (list) -> dict
        codes = {}
        for filepath in filepaths:
            with open(filepath) as fp:
                data = fp.read()
                code = ast.parse(data, filepath)
                codes[filepath] = code
        return codes

    def _filter_import_nodes(self, codes):
        # type: (dict) -> dict
        import_nodes = {}
        for filepath, code in codes.iteritems():
            import_nodes[filepath] = []
            for node in ast.walk(code):
                if not isinstance(node, ast.ImportFrom) and not isinstance(node, ast.Import):
                    continue
                # import statements that don't occur at the top level could lead to circular dependencies.
                if node.col_offset > ImportStatement.TOP_LEVEL and not self.allow_scoped_imports:
                    self._logger.debug("%s:%d:%d ignored scoped import; use --allow-scoped-imports to allow them.",
                            filepath, node.lineno, node.col_offset)
                    continue
                import_nodes[filepath].append(node)
        return import_nodes

    def _reify_import_nodes(self, import_nodes):
        # type: (dict) -> dict
        import_stmts = {}
        for filepath, nodes in import_nodes.iteritems():
            import_stmts[filepath] = []
            for node in nodes:
                stmt = ImportStatement.derive_from_ast_node(filepath, node)
                import_stmts[filepath].extend(stmt)
        return import_stmts

    def _resolve_import_stmts(self, import_stmts):
        # type: (dict) -> dict
        resolved_imports_sourcemap = {}
        for filepath, import_stmts in import_stmts.iteritems():
            resolved_imports_sourcemap[filepath] = []
            for import_stmt in import_stmts:
                resolved_import = import_stmt.resolve(self.workspace)
                resolved_imports_sourcemap[filepath].append(resolved_import)
            resolved_imports_sourcemap[filepath].sort(key=operator.attrgetter('bazel_path'))
        return resolved_imports_sourcemap

    def _synthesize_imports(self, imports_sourcemap):
        # type: (dict) -> dict
        manual_imports_sourcemap = {}
        for filepath, resolved_imports in imports_sourcemap.iteritems():
            manual_imports = resolved_imports[:]
            for module in self.manual_imports.get(filepath, []):
                manual_import = ImportStatement.synthesize(filepath, module)
                resolved_manual_import = manual_import.resolve(self.workspace)
                manual_imports.append(resolved_manual_import)
            manual_imports_sourcemap[filepath] = manual_imports
        return manual_imports_sourcemap

    def _synthesize_dependencies(self, imports_sourcemap):
        # type: (dict) -> dict
        manual_dependencies_sourcemap = {}
        for filepath, resolved_imports in imports_sourcemap.iteritems():
            manual_dependencies = resolved_imports[:]
            for bazel_path in self.manual_dependencies.get(filepath, []):
                manual_dependencies.append(ResolvedImport.make_manual_import(filepath, bazel_path))
            manual_dependencies_sourcemap[filepath] = manual_dependencies
        return manual_dependencies_sourcemap

    def _apply_pattern_deps(self, imports_sourcemap):
        # type: (dict) -> dict
        pattern_deps_sourcemap = {}
        patterns = {regex: re.compile(regex) for regex in self.pattern_deps}
        for filepath, resolved_imports in imports_sourcemap.iteritems():
            pattern_deps_imports = resolved_imports[:]
            for regexp, pattern_deps in self.pattern_deps.iteritems():
                if patterns[regexp].match(filepath) is None:
                    continue
                for module in pattern_deps.get("manual_imports", []):
                    manual_import = ImportStatement.synthesize(filepath, module)
                    resolved_manual_import = manual_import.resolve(self.workspace)
                    pattern_deps_imports.append(resolved_manual_import)
                for bazel_path in pattern_deps.get("manual_dependencies", []):
                    pattern_deps_imports.append(ResolvedImport.make_manual_import(filepath, bazel_path))
            pattern_deps_sourcemap[filepath] = pattern_deps_imports
        return pattern_deps_sourcemap

    def _build_library_targets(self, imports_sourcemap, build_template):
        # type: (dict, BazelBuildTemplate) -> BazelBuildTemplate
        for filepath in sorted(imports_sourcemap):
            resolved_imports = imports_sourcemap[filepath]
            deps = set()
            for resolved_import in resolved_imports:
                bazel_path = resolved_import.bazel_path
                if bazel_path is None:
                    continue
                deps.add(bazel_path)
            deps = list(deps)
            deps.sort()
            data = set()
            for bazel_path in self.manual_data_dependencies.get(filepath, []):
                data.add(bazel_path)
            data = list(data)
            data.sort()
            build_template.add_load_stmt(Ramsay.RULESET, Ramsay.LIBRARY_TARGET)
            build_template.add_library(name=to_safe_target_name(filepath), srcs=[filepath], deps=deps, data=data)
        return build_template

    def _build_test_targets(self, imports_sourcemap, build_template):
        # type: (dict, BazelBuildTemplate) -> BazelBuildTemplate
        for filepath in sorted(imports_sourcemap):
            if not filepath.startswith(Ramsay.TEST_PREFIX):
                continue
            if filepath in self.ignored_test_files:
                continue
            resolved_imports = imports_sourcemap[filepath]
            deps = set()
            for resolved_import in resolved_imports:
                bazel_path = resolved_import.bazel_path
                if bazel_path is None:
                    continue
                deps.add(bazel_path)
            deps = list(deps)
            deps.sort()
            data = set()
            for bazel_path in self.manual_data_dependencies.get(filepath, []):
                data.add(bazel_path)
            data = list(data)
            data.sort()
            build_template.add_load_stmt(Ramsay.RULESET, Ramsay.TEST_TARGET)
            build_template.add_test(name=to_safe_target_name(Ramsay.TEST_PREFIX + filepath), srcs=[filepath], deps=deps, data=data)
        return build_template

    def _build_shared_library_target(self, imports_sourcemap, build_template):
        # type: (dict, BazelBuildTemplate) -> BazelBuildTemplate
        deps = set()
        for filepath in sorted(imports_sourcemap):
            if filepath.startswith(Ramsay.TEST_PREFIX):
                continue
            bazel_path = to_safe_target_name(filepath)
            deps.add(bazel_path)
        deps = list(deps)
        deps.sort()
        build_template.add_load_stmt(Ramsay.RULESET, Ramsay.LIBRARY_TARGET)
        build_template.add_library(name="python_shared_library", deps=deps)
        return build_template

    def _append_post_sections(self, build_template):
        # type: (BazelBuildTemplate) -> BazelBuildTemplate
        for post_section in self.post_sections:
            build_template.add_post_section(post_section)
        return build_template


class Workspace:
    THE_FILENAME = "WORKSPACE"

    _logger = logging.getLogger(__name__)

    def __init__(self, root, module_aliases, ignored_modules, third_party_modules):
        if not os.path.isabs(root):
            raise ValueError("{} is not an absolute path".format(root))
        if not os.path.isdir(root):
            raise ValueError("{} is not a directory".format(root))
        if not os.path.isfile(os.path.join(root, "WORKSPACE")):
            raise ValueError("{} is not a Bazel workspace directory".format(root))
        self.root = root
        self.initialized = False
        self.module_aliases = module_aliases
        self.ignored_modules = ignored_modules
        self.third_party_modules = third_party_modules

    @classmethod
    def from_config(cls, config):
        # type: (Config) -> Workspace
        return Workspace(
                config.workspace_dir,
                config.module_aliases,
                config.ignored_modules,
                config.third_party_modules)

    @classmethod
    def find_workspace_abs_dirpath(cls, path=os.getcwd()):
        # type: (str) -> str
        """
        Finds the absolute path to the Bazel workspace directory, starting at the
        given path. If the workspace directory couldn't be found, will return None
        instead.
        """
        path = os.path.realpath(path)
        while True:
            if path is "/":
                return None
            elif not os.path.exists(os.path.join(path, Workspace.THE_FILENAME)):
                path = os.path.dirname(path)
                continue
            else:
                return path

    def is_system_module(self, module):
        # type: (str) -> bool
        components = module.split(".")
        assert(len(components) > 0)
        assert(len(components[0]) > 0)
        return module in SYSTEM_MODULES or \
                components[0] in SYSTEM_MODULES or \
                self.module_aliases.get(module, module) in SYSTEM_MODULES or \
                self.module_aliases.get(components[0], components[0]) in SYSTEM_MODULES

    def is_ignored_module(self, module):
        # type: (str) -> bool
        components = module.split(".")
        assert(len(components) > 0)
        assert(len(components[0]) > 0)
        return module in self.ignored_modules or \
                components[0] in self.ignored_modules or \
                self.module_aliases.get(module, module) in self.ignored_modules or \
                self.module_aliases.get(components[0], components[0]) in self.ignored_modules

    def is_third_party_module(self, module):
        # type: (str) -> bool
        components = module.split(".")
        assert(len(components) > 0)
        assert(len(components[0]) > 0)
        return module in self.third_party_modules or \
                components[0] in self.third_party_modules or \
                self.module_aliases.get(module, module) in self.third_party_modules or \
                self.module_aliases.get(components[0], components[0]) in self.third_party_modules

    def map_absolute_module(self, module, name):
        # type: (str) -> ("file"|"directory", str)
        """
        A import like 'from foo.bar import bazbar' could reference a symbol from the following files:

          * foo/bar/__init__.py
          * foo/bar/bazbar/__init__.py
          * foo/bar.py
          * foo/bar/bazbar.py
        """
        components = module.split(".")
        assert(len(components) > 0)

        if name is not None:
            components.append(name)

        workspace_relative_path = "/".join(components)
        assert(not os.path.isabs(workspace_relative_path))

        path = os.path.realpath(os.path.join(os.getcwd(), workspace_relative_path))
        if os.path.isdir(path):
            self._logger.debug("mapped absolute module '%s' to relative directory path '%s'", module, path)
            return ("dir", path)
        path = path + ".py"
        if os.path.isfile(path):
            self._logger.debug("mapped absolute module '%s' to relative file path '%s'", module, path)
            return ("file", path)

        path = os.path.realpath(os.path.join(self.root, workspace_relative_path))
        if os.path.isdir(path):
            self._logger.debug("mapped absolute module '%s' to absolute directory path '%s'", module, path)
            return ("dir", path)
        path = path + ".py"
        if os.path.isfile(path):
            self._logger.debug("mapped absolute module '%s' to absolute file path '%s'", module, path)
            return ("file", path)

        if name is None:
            return (None, None)
        components.pop()

        workspace_relative_path = "/".join(components)
        assert(not os.path.isabs(workspace_relative_path))

        path = os.path.realpath(os.path.join(os.getcwd(), workspace_relative_path))
        if os.path.isdir(path):
            self._logger.debug("mapped absolute module '%s' to relative directory path '%s'", module, path)
            return ("dir", path)
        path = path + ".py"
        if os.path.isfile(path):
            self._logger.debug("mapped absolute module '%s' to relative file path '%s'", module, path)
            return ("file", path)

        path = os.path.realpath(os.path.join(self.root, workspace_relative_path))
        if os.path.isdir(path):
            self._logger.debug("mapped absolute module '%s' to absolute directory path '%s'", module, path)
            return ("dir", path)
        path = path + ".py"
        if os.path.isfile(path):
            self._logger.debug("mapped absolute module '%s' to absolute file path '%s'", module, path)
            return ("file", path)

        return (None, None)

    def map_relative_module(self, filepath, module, level):
        # type: (str, int) -> ("file"|"directory", str)
        components = module.split(".")
        assert(len(components) > 0)
        module = "/".join(components)
        path = os.path.join(self.up_by(os.path.realpath(filepath), level), module)
        if os.path.isdir(path):
            self._logger.debug("mapped relative module '%s' to relative directory path '%s'", module, path)
            return ("dir", path)
        path = path + ".py"
        if os.path.isfile(path):
            self._logger.debug("mapped relative module '%s' to relative file path '%s'", module, path)
            return ("file", path)
        return (None, None)

    def absolute(self, path):
        return os.path.join(self.root, path)

    def relative(self, path):
        return os.path.relpath(path, self.root)

    def up_by(self, path, num):
        while num > 0:
            path = os.path.split(path)[0]
            num = num - 1
        return path

    def map_to_pypi_target(self, module):
        components = module.split(".")
        return self.module_aliases.get(components[0], components[0])

    def __str__(self):
        return self.root


class Config:
    FILENAME = ".ramsayrc"
    DEFAULT = {
        "workspace_dir": None,
        "module_aliases": {},
        "ignored_modules": [],
        "ignored_files": [],
        "ignored_test_files": [],
        "manual_imports": {},
        "manual_dependencies": {},
        "manual_data_dependencies": {},
        "pattern_deps": {},
        "post_sections": [],
        "third_party_modules": [],
        "allow_scoped_imports": False,
        "generate_library_targets": True,
        "generate_test_targets": True,
        "generate_shared_library": True,
        "enable_debug": False,
    }

    _logger = logging.getLogger(__name__)

    def __init__(self, workspace_dir, module_aliases, ignored_modules, ignored_files, ignored_test_files,
            manual_imports, manual_dependencies, manual_data_dependencies, pattern_deps, post_sections,
            third_party_modules, allow_scoped_imports, generate_library_targets, generate_test_targets,
            generate_shared_library, enable_debug):
        self.workspace_dir = workspace_dir
        self.module_aliases = module_aliases
        self.ignored_modules = ignored_modules
        self.ignored_files = ignored_files
        self.ignored_test_files = ignored_test_files
        self.manual_imports = manual_imports
        self.manual_dependencies = manual_dependencies
        self.manual_data_dependencies = manual_data_dependencies
        self.pattern_deps = pattern_deps
        self.post_sections = post_sections
        self.third_party_modules = third_party_modules
        self.allow_scoped_imports = allow_scoped_imports
        self.generate_library_targets = generate_library_targets
        self.generate_test_targets = generate_test_targets
        self.generate_shared_library = generate_shared_library
        self.enable_debug = enable_debug

    @classmethod
    def from_args(cls, args):
        # type: (argparse.Namespace) -> Config
        args_as_dict = vars(args)
        args_as_dict.update({
            "third_party_modules": Config._query_bazel_for_third_party_deps()
        })
        cascaded_config = Config._cascade_configs(Config.DEFAULT.copy(), args_as_dict)
        cls._logger.debug("initial configuration:")
        for key in sorted(cascaded_config):
            cls._logger.debug("  %s:%s", key, cascaded_config[key])

        workspace_ramsayrc_filepath = os.path.join(cascaded_config["workspace_dir"], Config.FILENAME)
        if os.path.exists(workspace_ramsayrc_filepath):
            workspace_ramsayrc = json.loads(open(workspace_ramsayrc_filepath).read())
            cascaded_config = Config._cascade_configs(cascaded_config, workspace_ramsayrc)
            cls._logger.debug("with workspace configuration:")
            for key in sorted(cascaded_config):
                cls._logger.debug("  %s:%s", key, cascaded_config[key])

        dirpath_it = os.getcwd()
        ramsayrc_filepaths = []

        if not os.path.exists(os.path.join(dirpath_it, Config.FILENAME)):
            ramsayrc_filepaths.append(('missing .ramsayrc file', Config.DEFAULT))
            dirpath_it = os.path.realpath(os.path.join(dirpath_it, os.pardir))

        while dirpath_it != cascaded_config["workspace_dir"]:
            ramsayrc_filepath_it = os.path.join(dirpath_it, Config.FILENAME)
            if os.path.exists(ramsayrc_filepath_it):
                ramsayrc_filepaths.append((ramsayrc_filepath_it, json.loads(open(ramsayrc_filepath_it).read())))
            dirpath_it = os.path.realpath(os.path.join(dirpath_it, os.pardir))
        ramsayrc_filepaths.reverse()

        for (ramsayrc_filepath, ramsayrc) in ramsayrc_filepaths:
            cascaded_config = Config._cascade_configs(cascaded_config, ramsayrc)
            cls._logger.debug("with cascaded configuration from %s:", ramsayrc_filepath)
            for key in sorted(cascaded_config):
                cls._logger.debug("  %s:%s", key, cascaded_config[key])

        return Config(
                cascaded_config["workspace_dir"],
                cascaded_config["module_aliases"],
                cascaded_config["ignored_modules"],
                cascaded_config["ignored_files"],
                cascaded_config["ignored_test_files"],
                cascaded_config["manual_imports"],
                cascaded_config["manual_dependencies"],
                cascaded_config["manual_data_dependencies"],
                cascaded_config["pattern_deps"],
                cascaded_config["post_sections"],
                cascaded_config["third_party_modules"],
                cascaded_config["allow_scoped_imports"],
                cascaded_config["generate_library_targets"],
                cascaded_config["generate_test_targets"],
                cascaded_config["generate_shared_library"],
                cascaded_config["enable_debug"])

    @classmethod
    def _cascade_configs(cls, dest, src):
        # properties that are set only once
        if not dest["workspace_dir"] and src["workspace_dir"]:
            dest["workspace_dir"] = src["workspace_dir"]

        # properties that are inherited by merging
        dest["module_aliases"].update(src.get("module_aliases", Config.DEFAULT["module_aliases"].copy()))
        dest["ignored_modules"].extend(src.get("ignored_modules", Config.DEFAULT["ignored_modules"][:]))
        dest["ignored_files"].extend(src.get("ignored_files", Config.DEFAULT["ignored_files"][:]))
        dest["ignored_test_files"].extend(src.get("ignored_test_files", Config.DEFAULT["ignored_test_files"][:]))
        dest["pattern_deps"].update(src.get("pattern_deps", Config.DEFAULT["pattern_deps"].copy()))
        dest["third_party_modules"].extend(src.get("third_party_modules", Config.DEFAULT["third_party_modules"][:]))

        # properties that are inherited by overwriting
        dest["allow_scoped_imports"] = src.get("allow_scoped_imports", dest["allow_scoped_imports"])
        dest["generate_library_targets"] = src.get("generate_library_targets", dest["generate_library_targets"])
        dest["generate_test_targets"] = src.get("generate_test_targets", dest["generate_test_targets"])
        dest["generate_shared_library"] = src.get("generate_shared_library", dest["generate_shared_library"])
        dest["enable_debug"] = src.get("enable_debug", dest["enable_debug"])

        # properties that aren't modified
        dest["manual_imports"] = src.get("manual_imports", Config.DEFAULT["manual_imports"].copy())
        dest["manual_dependencies"] = src.get("manual_dependencies", Config.DEFAULT["manual_dependencies"].copy())
        dest["manual_data_dependencies"] = src.get("manual_data_dependencies", Config.DEFAULT["manual_data_dependencies"].copy())
        dest["post_sections"] = src.get("post_sections", Config.DEFAULT["post_sections"][:])
        
        return dest

    @classmethod
    def _query_bazel_for_third_party_deps(cls):
        # type: () -> set
        deps = set()
        output = subprocess.check_output("bazel query //python2/third_party/... 2>/dev/null | cut -f2 -d: | sort", shell=True)
        for line in output.split('\n'):
            if not line:
                continue
            deps.add(line)
        return deps


class ImportStatement:
    TOP_LEVEL = 0

    _logger = logging.getLogger(__name__)

    def __init__(self, filepath, module, level, name, lineno, col_offset):
        self.filepath = filepath
        self.module = module
        self.level = level
        self.name = name
        self.lineno = lineno
        self.col_offset = col_offset

    @classmethod
    def derive_from_ast_node(cls, filepath, node):
        # type: (str, ast.Node) -> List[ImportStatement]
        if isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names]
            return [ImportStatement(
                filepath,
                node.module or name,
                node.level,
                name if node.module is not None else None,
                node.lineno,
                node.col_offset) for name in names]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
            return [ImportStatement(filepath, name, ImportStatement.TOP_LEVEL, None, node.lineno, node.col_offset)
                    for name in names]
        else:
            return []

    @classmethod
    def synthesize(cls, filepath, module):
        # type: (str, str) -> ImportStatement
        return ImportStatement(filepath, module, ImportStatement.TOP_LEVEL, None, -1, -1)

    def __str__(self):
        return """#<Import filepath={} module={} level={} name={}>""".format(
            self.filepath, self.module, self.level, self.name)

    def resolve(self, workspace):
        # type: (Workspace) -> ResolvedImport
        if self.level == ImportStatement.TOP_LEVEL:
            return self._resolve_absolute_module(workspace)
        else:
            return self._resolve_relative_module(workspace)

    def _resolve_absolute_module(self, workspace):
        # type: (Workspace) -> ResolvedImport
        """
        Resolves an absolute import (or, more precisely, a level-0 import), ie.:

        import foo                         (1)
        import foo.bar                     (2)
        import foo as bar                  (3)
        import foo.bar as baz              (4)
        from foo import bar                (5)
        from foo import bar as baz         (6)
        from foo.bar import baz            (7)
        from foo.bar import baz as bazbar  (8)

        These may look absolute on first sight, but loading the absolute_import module from __future__ *can* make the
        above imports refer to modules relatively.
        """

        # Parsing through the AST, we may find conditional imports that, say, are wrapped in a try-catch expression. The
        # programmer's intention is to enable and disable certain features at run-time. The programmer will have to
        # further control the inclusion of the module with the ignored_modules configuration project-level configuration
        # option.
        if workspace.is_ignored_module(self.module):
            self._logger.debug("%s:%d:%d parsed import as skipped; it's in the list of ignored modules.",
                    self.filepath, self.lineno, self.col_offset)
            return ResolvedImport.make_skipped_import(self.filepath, self.module, self.level, self.name)

        # A system import is a module guaranteed to be present on every system. We can't generate a dependency for them,
        # because we don't have a facility to manage them.
        if workspace.is_system_module(self.module):
            self._logger.debug("%s:%d:%d parsed import as site import.",
                    self.filepath, self.lineno, self.col_offset)
            return ResolvedImport.make_site_import(self.filepath, self.module, self.name)

        # Give highest precedence to third-party modules.
        if workspace.is_third_party_module(self.module):
            self._logger.debug("%s:%d:%d parsed import as third-party import.",
                    self.filepath, self.lineno, self.col_offset)
            return ResolvedImport.make_requirement_import(
                self.filepath, self.module, self.name,
                "//python2/third_party/pypi:{}".format(workspace.map_to_pypi_target(self.module)))

        position, path = workspace.map_absolute_module(self.module, self.name)
        if position is "file":
            rel_path = workspace.relative(path)
            return ResolvedImport.make_local_import(
                rel_path, self.module, self.level, self.name,
                "//{}:{}".format(os.path.dirname(rel_path), to_safe_target_name(os.path.basename(rel_path))))
        elif position is "dir":
            rel_path = workspace.relative(path)
            if os.path.relpath(path, os.getcwd()) is ".":
                return ResolvedImport.make_skipped_import(rel_path, self.module, self.level, self.name)
            else:
                return ResolvedImport.make_local_import(
                    rel_path, self.module, self.level, self.name,
                    "//{}:__init___py".format(rel_path))
        else:
            raise Exception("another unhandled way of importing a module: {}".format(self))

    def _resolve_relative_module(self, workspace):
        # type: (Workspace) -> ResolvedImport
        position, path = workspace.map_relative_module(
            self.filepath,
            self.module,
            self.level)
        if position is "file":
            rel_path = workspace.relative(path)
            return ResolvedImport.make_local_import(
                    self.filepath, self.module, self.level, self.name,
                    "//{}:{}".format(os.path.dirname(rel_path), to_safe_target_name(os.path.basename(path))))
        elif position is "dir":
            rel_path = workspace.relative(path)
            if os.path.relpath(path, os.getcwd()) is ".":
                return ResolvedImport.make_skipped_import(rel_path, self.module, self.level, self.name)
            else:
                return ResolvedImport.make_local_import(
                    self.filepath, self.module, self.level, self.name,
                    "//{}:__init___py".format(rel_path))
        else:
            raise Exception("failed to resolve module '{}'".format(self.module))


class ResolvedImport:
    _logger = logging.getLogger(__name__)

    def __init__(self, filepath, module, level, name, scope, bazel_path):
        self.filepath = filepath
        self.module = module
        self.level = level
        self.name = name
        self.scope = scope
        self.bazel_path = bazel_path

    @classmethod
    def make_site_import(cls, filepath, module, name):
        # type: (str, str, str) -> ResolvedImport
        return ResolvedImport(filepath, module, ImportStatement.TOP_LEVEL, name, "site", None)

    @classmethod
    def make_requirement_import(cls, filepath, module, name, bazel_path):
        # type: (str, str, str, str) -> ResolvedImport
        return ResolvedImport(filepath, module, ImportStatement.TOP_LEVEL, name, "requirement", bazel_path)

    @classmethod
    def make_local_import(cls, filepath, module, level, name, bazel_path):
        # type: (str, str, int, str, str) -> ResolvedImport
        return ResolvedImport(filepath, module, level, name, "local", bazel_path)

    @classmethod
    def make_skipped_import(cls, filepath, module, level, name):
        # type: (str, str, int, str) -> ResolvedImport
        return ResolvedImport(filepath, module, level, name, "skipped", None)

    @classmethod
    def make_manual_import(cls, filepath, bazel_path):
        # type: (str, str) -> ResolvedImport
        return ResolvedImport(filepath, None, ImportStatement.TOP_LEVEL, None, "manual", bazel_path)

    def __str__(self):
        return """#<ResolvedImport filepath={} module={} level={} name={} scope={} bazel_path={}>""".format(
            self.filepath, self.module, self.level, self.name, self.scope, self.bazel_path)


class BazelBuildTemplate:
    _logger = logging.getLogger(__name__)

    def __init__(self):
        self.packages = [SkylarkPackageStatement("default_visibility", ["//visibility:public"])]
        self.loads = {}
        self.libraries = []
        self.tests = []
        self.post_sections = []

    def add_package_stmt(self, property, value):
        # type: (str, Any) -> None
        self.packages.append(SkylarkPackageStatement(property, value))

    def add_load_stmt(self, module, macro):
        # type: (str, str) -> None
        """
        Adds a 'load' statement to the generated BUILD file.
        """
        if module not in self.loads:
            self.loads[module] = SkylarkLoadStatement(module)
        self.loads[module].macros.add(macro)

    def add_library(self, name, srcs=[], deps=[], data=[], pythonroot="//"):
        # type: (str, list[str], list[str], list[str], str) -> None
        """
        Adds a 'pyz_library' target to the generated BUILD file.
        """
        self.libraries.append(PyzLibraryTarget(name, srcs, deps, data, pythonroot))

    def add_test(self, name, srcs, deps=[], data=[], pythonroot="//", interpreter_path="python2.7"):
        # type: (str, list[str], list[str], list[str], str, str) -> None
        """
        Adds a 'pyz_test' target to the generated BUILD file.
        """
        self.tests.append(PyzTestTarget(name, srcs, deps, data, pythonroot, interpreter_path))

    def add_post_section(self, contents):
        # type: (str) -> None
        """
        Adds a section of text to the end of the BUILD file.
        """
        self.post_sections.append(contents)

    def __str__(self):
        loader = jinja2.DictLoader({
            "BUILD": """\
#
#   This file was auto-generated by ramsay, the BUILD file generator for Python code.
#   DO NOT EDIT.
#

{% for package in this.packages %}
    {%- include "package" %}
{% endfor %}
{% for module, load in this.loads.items() %}
    {%- include "load" %}
{% endfor %}
{% for library in this.libraries %}
    {%- include "pyz_library" %}

{% endfor %}{% for test in this.tests %}
    {%- include "pyz_test" %}

{% endfor %}

{% for post_section in this.post_sections %}
{{ post_section }}
{% endfor %}
""",
            "package": """package({{ package.property }} = {{ package.value|tojson }})""",
            "load": """load({{ load.module|tojson }}, {{ load.macros|map('tojson')|join(", ") }})""",
            "pyz_library": """\
pyz_library(
    name = {{ library.name|tojson }},
    srcs = {{ library.srcs|tojson }},
    deps = {{ library.deps|tojson }},
    data = {{ library.data|tojson }},
    pythonroot = {{ library.pythonroot|tojson }},
)
""",
            "pyz_test": """\
pyz_test(
    name = {{ test.name|tojson }},
    srcs = {{ test.srcs|tojson }},
    deps = {{ test.deps|tojson }},
    data = {{ test.data|tojson }},
    pythonroot = {{ test.pythonroot|tojson }},
    interpreter_path = {{ test.interpreter_path|tojson }},
)
"""
        })
        env = jinja2.Environment(loader=loader)
        template = env.get_template("BUILD")
        return template.render(this=self)


class SkylarkPackageStatement:
    def __init__(self, property, value):
        self.property = property
        self.value = value


class SkylarkLoadStatement:
    def __init__(self, module, macros=set()):
        self.module = module
        self.macros = macros


class PyzLibraryTarget:
    def __init__(self, name, srcs, deps, data, pythonroot):
        self.name = name
        self.srcs = srcs
        self.deps = deps
        self.data = data
        self.pythonroot = pythonroot


class PyzTestTarget:
    def __init__(self, name, srcs, deps, data, pythonroot, interpreter_path):
        self.name = name
        self.srcs = srcs
        self.deps = deps
        self.data = data
        self.pythonroot = pythonroot
        self.interpreter_path = interpreter_path


def to_safe_target_name(s):
    # type: (str) -> str
    s = s.lower()
    return re.sub("[^a-z0-9]", "_", s)


if __name__ == "__main__":
    main(sys.argv)
