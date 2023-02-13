import sys
import os
import collections
from subprocess import run

from lxml import etree

HELP = """

Fromrelax mode
--------------
Transforms a RelaxNG schema into an equivalent xsd schema using
James Clark's trang library.
For this, you must have java installed.

Analyse mode
-------------
Given an XML schema file,
produces a tab-separated list of elements defined in the schema,
with columns

    (element name) (simple or complex) (mixed or pure content)

TEI mode
--------
Analyses the TEI schema.
If you pass an optional customized TEI schema, it will be passed separately,
and the resulting info will be merged with the analysis result of the
complete TEI schema.
The information from the customised schema overrides the information from the
complete TEI schema.
The complete TEI schema is part of this package, you do not have to provide it.
It has been generated on with the online TEI-Roma tool at
https://roma.tei-c.org/startroma.php.


USAGE

Command line:

xmlschema tei [schemafile.xsd]
xmlschema analyse {schemafile.xsd}
xmlschema fromrelax {schemafile.rng}

CAVEAT

This code has only been tested on a single xsd, converted from a relaxRNG
file produced by a customisation of TEI.

It could very well be that I have missed parts of the semantics of XML-Schema.
"""


class Analysis:
    types = set(
        """
        simpleType
        complexType
        """.strip().split()
    )

    notInteresting = set(
        """
        attribute
        attributeGroup
        group
        """.strip().split()
    )

    def __init__(self, baseSchema, override=None, debug=False):
        """Extracts meaningful information from an XML Schema.

        When parsing XML it is sometimes needed to know the properties of the current
        element, especially whether it allows mixed content or not.

        If it does not, it is safe to discard white-space, otherwise not.

        Moreover, if there are two adjacent elements, each containing text,
        are the string at the end of the first element and the string at the start of the
        second element part of the same word?

        If both elements are contained in a element that does not allow mixed content,
        they are separate words (the XML-elements are used as data containers);
        otherwise they belong to the same word (the XML-elements annotate a piece
        of string).

        Parameters
        ----------
        baseSchema: string
            The path of the xsd file that acts as the base schema that we want
            to analyse.
        override: string, optional None
            The path of another schema intended to override parts of the baseSchema.
        debug: boolean, optional False
            Whether to run in debug mode or not.
            In debug mode more information is printed on the console.
        """

        self.debug = debug

        try:
            with open(baseSchema) as fh:
                tree = etree.parse(fh)

            self.root = tree.getroot()

            self.oroot = None

            if override is not None:
                with open(baseSchema) as fh:
                    otree = etree.parse(fh)

                self.oroot = otree.getroot()

            self.good = True

        except Exception as e:
            msg = f"Could not read and parse {baseSchema}"
            if override is not None:
                msg += " or {override}"
            print(msg)
            print(str(e))
            self.good = False

    @staticmethod
    def eKey(x):
        """Sort the dict with element definitions.

        Parameters
        ----------
        x: (str, dict)
            The element name and the element info.

        Returns
        -------
        tuple
            The members are such that definitions from other than xs:element come first,
            and within xs:element those that are "abstract" come first.
        """
        name = x[0]
        tag = x[1]["tag"]
        abstract = x[1]["abstract"]

        return (
            "0" if tag == "simpleType" else "1" if tag == "complexType" else tag,
            "" if abstract else "x",
            name,
        )

    def interpret(self, asTsv=False):
        """Reads the xsd and interprets the element definitions.

        The definitions are read with the module `lxml`.

        For each definition of a name certain attributes are remembered, e.g.
        the *kind*, the presence of a *mixed* attribute, whether it is a
        *substitutionGroup* or *extension*, and whether it is *abstract*.

        When elements refer to a *substitutionGroup*, they need to get
        the *kind* and *mixed* attributes of that group.

        When elements refer to a *base*, they need to get
        the *kind* and *mixed* attributes of an extension with that *base*.

        After an initial parse of the XSD file, we do a variable number of
        resolving rounds, where we chase the substitution groups and
        extensions, until nothing changes anymore.

        Parameters
        ----------
        asTsv: boolean, optional False
            If True, the result is delivered as a TSV text,
            otherwise as a list,

        Returns
        -------
        str | list
            One line/item per element.
            Each line has: element name, element kind, mixed status.

            The absence of the element *kind* and *mixed* status are indicated
            with `---` in the TSV and with the `None` value in the list.
            If all went well, there are n such absences!
        """

        debug = self.debug
        root = self.root
        types = self.types

        definitions = {}
        redefinitions = collections.Counter()

        def findDefs(node, definingName, topDef):
            """Inner function to walk through the xsd and get definitions.

            This function is called recursively for child nodes.

            Parameters
            ----------
            node: Object
                The current node.
            definingName: string | void
                If this has a value, we are underneath a definition.

            topDef: boolean
                If we are underneath a definition, this indicates
                we are at the top-level of that definition.
            """
            tag = etree.QName(node.tag).localname

            name = node.get("name")
            abstract = node.get("abstract") == "true"
            mixed = node.get("mixed") == "true"
            subs = node.get("substitutionGroup")

            if definingName:
                if topDef:
                    if tag in types:
                        definitions[definingName]["kind"] = (
                            "simple" if tag == "simpleType" else "complex"
                        )
                        if mixed:
                            definitions[definingName]["mixed"] = mixed
                else:
                    if tag == "extension":
                        base = node.get("base")
                        if base:
                            definitions[definingName]["base"] = base

            if name and tag not in self.notInteresting:
                if name in definitions:
                    redefinitions[name] += 1
                else:
                    definitions[name] = dict(
                        tag=tag, abstract=abstract, mixed=mixed, subs=subs
                    )

            if definingName:
                defining = definingName
                top = False
            else:
                isElementDef = name and tag == "element"
                defining = name if isElementDef else False
                top = True if defining else False

            for child in node.iterchildren(tag=etree.Element):
                findDefs(child, defining, top)

        findDefs(root, False, False)
        if debug:
            self.printElems()
        self.resolve(definitions)

        baseDefinitions = definitions

        oroot = self.oroot

        def repMixed(m):
            return "-----" if m is None else "mixed" if m else "pure"

        def repKind(k):
            return "-----" if k is None else k

        self.overrides = {}

        if oroot is not None:
            definitions = {}
            redefinitions = collections.Counter()
            findDefs(oroot, False, False)
            if debug:
                self.printElems()
            self.resolve(definitions)

            for (name, odef) in definitions.items():
                if name in definitions:
                    baseDef = baseDefinitions[name]
                    baseKind = repKind(baseDef.get("kind", None))
                    baseMixed = repMixed(baseDef.get("mixed", None))
                    oKind = repKind(odef.get("kind", None))
                    oMixed = repMixed(odef.get("mixed", None))
                    transRep = (
                        f"{baseKind} {baseMixed} ==> {oKind} {oMixed}"
                        if baseKind != oKind and baseMixed != oMixed
                        else f"{baseKind} ==> {oKind}"
                        if baseKind != oKind
                        else f"{baseMixed} ==> {oMixed}"
                        if baseMixed != oMixed
                        else None
                    )
                    if transRep is not None:
                        baseDefinitions[name] = odef
                    self.overrides[name] = transRep
        defs = tuple(
            (name, info.get("kind", None), info.get("mixed", None))
            for (name, info) in sorted(baseDefinitions.items(), key=self.eKey)
            if info["tag"] == "element" and not info["abstract"]
        )
        return (
            "\n".join(
                f"{name}\t{repKind(kind)}\t{repMixed(mixed)}"
                for (name, kind, mixed) in defs
            )
            if asTsv
            else defs
        )

    def resolve(self, definitions):
        """Resolve indirections in the definitions.

        After having read the complete XSD file,
        we can now dereference names and fill properties of their definitions
        in places where the names occur.
        """
        debug = self.debug

        def infer():
            changed = 0
            for (name, info) in definitions.items():
                if info["mixed"]:
                    continue

                other = info.get("base", info.get("subs", None))
                if other:
                    otherBare = other.split(":", 1)[-1]
                    otherInfo = definitions.get(otherBare, None)
                    if otherInfo is None:
                        print(f"Warning: {other} is not defined.")
                        continue
                    if otherInfo["mixed"]:
                        info["mixed"] = True
                        changed += 1
                    if info.get("kind", None) is None:
                        if otherInfo.get("kind", None):
                            info["kind"] = otherInfo["kind"]
                            changed += 1
                        else:
                            print(f"Warning: {other}.kind is not defined.")
                    if info.get("mixed", None) is None:
                        if otherInfo.get("mixed", None):
                            info["mixed"] = otherInfo["mixed"]
                            changed += 1
                        else:
                            print(f"Warning: {other}.mixed is not defined.")

            return changed

        i = 0

        while True:
            changed = infer()
            i += 1
            if changed:
                print(f"round {i:>3}: {changed:>3} changes")
                if debug:
                    self.printElems()
            else:
                break

    def printElems(self):
        """Pretty print the current state of definitions.

        Mainly for debugging.
        """
        definitions = self.definitions
        redefinitions = self.redefinitions

        for (name, info) in sorted(definitions.items(), key=self.eKey):
            tag = info["tag"]
            mixed = "mixed" if info["mixed"] else "-----"
            abstract = "abstract" if info["abstract"] else "--------"
            kind = info.get("kind", "---")
            subs = info.get("subs")
            subsRep = f"==> {subs}" if subs else ""
            base = info.get("base")
            baseRep = f"<== {base}" if base else ""
            print(
                f"{name:<30} in {tag:<20} "
                f"({kind:<7}) ({mixed}) ({abstract}) {subsRep}{baseRep}"
            )

        print("=============================================")
        for (name, amount) in sorted(redefinitions.items()):
            print(f"{amount:>3}x {name}")


TASKS = dict(
    tei={0, 1},
    analyse={1},
    fromrelax={1},
)


def main():
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(HELP)
        return 0

    if len(args) == 0:
        print(HELP)
        print("No task specified")
        return -1

    task = args.pop(0)
    nParams = TASKS.get(task, None)
    if nParams is None:
        print(HELP)
        print(f"Unrecognized task {task}")
        return -1

    if len(args) not in nParams:
        print(HELP)
        print(f"Wrong number of arguments ({len(args)} for {task}")
        return -1

    if task in {"tei", "fromrelax"}:
        myDir = os.path.dirname(os.path.abspath(__file__))

    if task in {"tei", "analyse"}:
        if task == "tei":
            baseSchema = f"{myDir}/tei/tei_all.xsd"
            override = args[0] if len(args) else None
        else:
            baseSchema = args[0]
            override = None

        A = Analysis(baseSchema, override=override, debug=False)
        if not A.good:
            return 1

        defs = A.interpret(asTsv=True)
        print(defs)
        print(f"!!!!!!!!!! {len(defs):>3} elements defined")
        if override:
            overrides = A.overrides
            same = sum(1 for x in overrides.items() if x[1] is None)
            distinct = len(overrides) - same
            print(f"!!!!!!!!!! {same:>3} identical overrides")
            print(f"!!!!!!!!!! {distinct:>3} changing overrides")
        for (name, trans) in sorted(x for x in A.overrides.items() if x[1] is not None):
            print(f"!!!!!!!!!! {name} {trans}")
        return 0

    if task == "fromrelax":
        schemaFile = args[0]
        myDir = os.path.dirname(os.path.abspath(__file__))
        trang = f"{myDir}/trang/trang.jar"
        schemaOut = schemaFile.removesuffix(".rng") + ".xsd"
        return run(f"java -jar {trang} {schemaFile} {schemaOut}", shell=True)


if __name__ == "__main__":
    exit(main())
