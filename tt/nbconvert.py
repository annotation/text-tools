import sys
import os
import re
from subprocess import run
from shutil import rmtree, copy


__pdoc__ = {}

HELP = """
python nbconvert.py inputDir outputDir

Converts all `.ipynb` files in *inputDir* to `.html` files in *outputDir*.
Copies all other files in *inputDir* to *outputDir*.

Makes sure that all links in the resulting html to one of the
original `.ipynb` files are transformed in links to the converted `.html` files.

Command switches

```
-h
--help
```

"""


def normpath(path):
    return None if path is None else path.replace("\\", "/")


HOME_DIR = normpath(os.path.expanduser("~"))


def expanduser(path):
    nPath = normpath(path)
    if nPath.startswith("~"):
        return f"{HOME_DIR}{nPath[1:]}"

    return nPath


def fileExists(path):
    """Whether a path exists as file on the file system.
    """
    return os.path.isfile(path)


def fileRemove(path):
    """Removes a file if it exists as file.
    """
    if fileExists(path):
        os.remove(path)


def fileCopy(pathSrc, pathDst):
    """Copies a file if it exists as file.

    Wipes the destination file, if it exists.
    """
    if fileExists(pathSrc):
        fileRemove(pathDst)
        copy(pathSrc, pathDst)


def clearTree(path):
    """Remove all files from a directory, recursively, but leave subdirs.

    Reason: we want to inspect output in an editor.
    But if we remove the directories, the editor looses its current directory
    all the time.

    Parameters
    ----------
    path:
        The directory in question. A leading `~` will be expanded to the user's
        home directory.
    """

    subdirs = []
    path = expanduser(path)

    with os.scandir(path) as dh:
        for (i, entry) in enumerate(dh):
            name = entry.name
            if name.startswith("."):
                continue
            if entry.is_file():
                os.remove(f"{path}/{name}")
            elif entry.is_dir():
                subdirs.append(name)

    for subdir in subdirs:
        clearTree(f"{path}/{subdir}")


def initTree(path, fresh=False, gentle=False):
    """Make sure a directory exists, optionally clean it.

    Parameters
    ----------
    path:
        The directory in question. A leading `~` will be expanded to the user's
        home directory.

        If the directory does not exist, it will be created.

    fresh: boolean, optional False
        If True, existing contents will be removed, more or less gently.

    gentle: boolean, optional False
        When existing content is removed, only files are recursively removed, not
        subdirectories.
    """

    path = expanduser(path)
    exists = os.path.exists(path)
    if fresh:
        if exists:
            if gentle:
                clearTree(path)
            else:
                rmtree(path)

    if not exists or fresh:
        os.makedirs(path, exist_ok=True)


def task(inputDir, outputDir):
    if not os.path.isdir(inputDir):
        print(f"Input directory does not exist: {inputDir}")
        return 1
    initTree(outputDir, fresh=True)

    nbext = ".ipynb"

    convertedNotebooks = []

    def escapeSpace(x):
        return x.replace(" ", "\\ ")

    def doSubDir(path):
        subInputDir = inputDir if path == "" else f"{inputDir}/{path}"
        subOutputDir = outputDir if path == "" else f"{outputDir}/{path}"
        initTree(subOutputDir)

        theseNotebooks = []

        with os.scandir(subInputDir) as dh:
            for entry in dh:
                name = entry.name
                subPath = name if path == "" else f"{path}/{name}"
                if entry.is_dir():
                    doSubDir(subPath)
                elif name.endswith(nbext):
                    theseNotebooks.append(name)
                else:
                    if not name.startswith("."):
                        fileCopy(f"{subInputDir}/{name}", f"{subOutputDir}/{name}")

        if len(theseNotebooks):
            command = "jupyter nbconvert --to html"
            inFiles = " ".join(
                f"{subInputDir}/{escapeSpace(name)}" for name in theseNotebooks
            )
            commandLine = f"{command} --output-dir={subOutputDir} {inFiles}"
            print(commandLine)
            run(commandLine, shell=True)
            for thisNotebook in theseNotebooks:
                convertedNotebooks.append(
                    (subOutputDir, thisNotebook.replace(nbext, ""))
                )

    doSubDir("")
    convertedPat = ")|(?:".join(re.escape(c[1]) for c in convertedNotebooks)

    LINK_RE = re.compile(
        rf"""
            \b
            (
                (?:
                    href|src
                )
                =
                ['"]
                (?:
                    [^'"]*/
                )?
                (?:
                    {convertedPat}
                )
            )
            (?:
                {nbext}
            )
            (
                ['"]
            )
        """,
        re.X,
    )

    def processLinks(text):
        return LINK_RE.sub(r"\1.html\2", text)

    print("fixing links to converted notebooks:")
    for (path, name) in convertedNotebooks:
        pathName = f"{path}/{name}.html"
        print(pathName)
        with open(pathName) as fh:
            text = fh.read()
        text = processLinks(text)
        with open(pathName, "w") as fh:
            fh.write(text)


def main():
    args = sys.argv[1:]
    if "-h" in args or "--help" in args or len(args) != 2:
        print(HELP)
        quit()

    return task(*args)


__pdoc__["task"] = HELP


if __name__ == "__main__":
    exit(main())
