from analysis import SchemaAnalysis


schemaFile = "resources/MD.xsd"

SA = SchemaAnalysis(schemaFile)
defs = SA.interpret()

print(defs)
