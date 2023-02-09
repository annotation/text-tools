import sys
import collections

from lxml import etree

HELP = """

Given an XML schema file,
produces a tab-separated list of elements defined in the schema,
with columns
    (element name) (simple or complex) (mixed or pure content)

USAGE

Command line:

python analysis.py schemafile.xsd

In a Python program:

from analysis import SchemaAnalysis
schemaFile = "MD.xsd"
SA = SchemaAnalysis(schemaFile)
defs = SA.interpret()
print(defs)

CAVEAT

This code has only been tested on a single xsd, converted from a relaxrng
file produced by a customisation of TEI.

It could very well be that I have missed parts of the sematics of XML Schema.
"""


class SchemaAnalysis:
    """Extracts meaningfull information from an XML Schema.

    When parsing XML it is sometimes needed to know the properties of the current
    element, especially whether it allows mixed content or not.

    If it does not, it is save to discard white-space, otherwise not.

    Moreover, if there are two adjacent elements, each containing text,
    are the string at the end of the first element and the string at the start of the
    second element part of the same word?

    If both elements are contained in a element that does not allow mixed content,
    they are separate words (the XML-elements are used as data containers);
    otherwise they belong to the same word (the XML-elements annotate a piece
    of string).
    """

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

    def __init__(self, schemaPath, debug=False):
        with open(schemaPath) as fh:
            tree = etree.parse(fh)

        self.debug = debug
        self.root = tree.getroot()

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
            Such that definitions from other than xs:element come first,
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

        The definitions are read with the module lxml.

        For each definition of a name certain attributes are rememebered, e.g.
        the kind, the presence of a mixed attribute, whether it is a
        substitutionGroup or extension, and whether it is abstract.

        When elements refer to a substitutionGroup, they need to get
        the kind and mixed attributes of that group.

        When elements refer to a base, they need to get
        the kind and mixed attributes of an extension with that base.

        After an initial parse of the XSD file, we do a variable number of
        resolving rounds, where we chase the substitution groups and
        extensions, untill nothing changes anymore.

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

            The absence of the element kind and mixed status are indicated
            with `---` in the TSV and with the `None` value in the list.
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
                If this has a value, we are under neath a definition.

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
        self.definitions = definitions
        self.redefinitions = redefinitions
        if debug:
            self.printElems()

        self.resolve()
        defs = tuple(
            (name, info.get("kind", None), info.get("mixed", None))
            for (name, info) in sorted(definitions.items(), key=self.eKey)
            if info["tag"] == "element" and not info["abstract"]
        )
        if asTsv:
            return "\n".join(
                f"""{name}\t{kind or "---"}\t{"mixed" if mixed else "pure"}"""
                for (name, kind, mixed) in defs
            )
        return defs

    def resolve(self):
        """Resolve indirections in the definitions.

        After having read the complete XSD file,
        we can now dereference names and fill properties of their definitions
        in places where the names occur.
        """
        debug = self.debug
        definitions = self.definitions

        def infer():
            changed = 0
            for (name, info) in definitions.items():
                if info["mixed"]:
                    continue

                other = info.get("base", info.get("subs", None))
                if other:
                    other = other.split(":", 1)[-1]
                    otherInfo = definitions[other]
                    if otherInfo["mixed"]:
                        info["mixed"] = True
                        changed += 1
                    if info.get("kind", None) is None and otherInfo.get("kind", None):
                        info["kind"] = otherInfo["kind"]
                        changed += 1

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

        redefinitions = self.redefinitions
        print("=============================================")
        for (name, amount) in sorted(redefinitions.items()):
            print(f"{amount:>3}x {name}")


def main():
    args = sys.argv[1:]
    if "-h" in args or "--help" in args or len(args) != 1:
        print(HELP)
        quit()

    schemaFile = args[0]
    SA = SchemaAnalysis(schemaFile, debug=False)
    defs = SA.interpret(asTsv=True)
    print(defs)


if __name__ == "__main__":
    exit(main())
