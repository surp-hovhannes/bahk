"""Strict JSON schema subset for private catalogue evidence; independent of matching."""

STRING = {"type": "string"}
BOOL = {"type": "boolean"}
INT = {"type": "integer"}


def obj(**properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def array(items):
    return {"type": "array", "items": items}


def validate_schema(value, schema):
    """Validate the deliberately small strict JSON schema subset used above."""
    kind = schema["type"]
    valid = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": type(value) is bool,
        "integer": type(value) is int,
    }[kind]
    if not valid or ("enum" in schema and value not in schema["enum"]):
        raise ValueError("schema")
    if kind == "integer" and not schema.get("minimum", value) <= value <= schema.get("maximum", value):
        raise ValueError("schema")
    if kind == "object":
        if set(value) != set(schema["properties"]):
            raise ValueError("schema")
        for key, subschema in schema["properties"].items():
            validate_schema(value[key], subschema)
    elif kind == "array":
        for item in value:
            validate_schema(item, schema["items"])
