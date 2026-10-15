"""Local DDL primitives; no runtime or earlier proposal dependency."""

def col(name, typ, constraint, reason):
    return (name, typ, constraint, reason)

def ident(reason="沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用"):
    return col("id", "TEXT", "PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)", reason)

def text(name, reason, required=False, limit=None):
    checks = []
    if required:
        checks.append(f"length(trim({name})) > 0")
    if limit:
        checks.append(f"length({name}) <= {limit}")
    sql = "NOT NULL" if required else ""
    if checks:
        sql += " CHECK(" + " AND ".join(checks) + ")"
    return col(name, "TEXT", sql.strip(), reason)

def enum(name, values, reason, required=True, default=None):
    v = ",".join("'" + x + "'" for x in values)
    sql = "NOT NULL " if required else ""
    if default is not None:
        sql += f"DEFAULT '{default}' "
    return col(name, "TEXT", sql + f"CHECK({name} IN ({v}))", reason)

def boolean(name, reason, required=True, default=None):
    sql = "NOT NULL " if required else ""
    if default is not None:
        sql += f"DEFAULT {default} "
    return col(name, "INTEGER", sql + f"CHECK({name} IN (0,1))", reason)

def number(name, typ, low, high, reason, required=False, default=None):
    sql = "NOT NULL " if required else ""
    if default is not None:
        sql += f"DEFAULT {default} "
    condition = f"{name} >= {low}"
    if high is not None:
        condition += f" AND {name} <= {high}"
    return col(name, typ, sql + f"CHECK({condition})", reason)

def document(name, reason, shape="object", required=False, default=None):
    sql = "NOT NULL " if required else ""
    if default is not None:
        sql += "DEFAULT '" + default + "' "
    # CASE prevents malformed JSON evaluation. COALESCE prevents SQL NULL from
    # bypassing the required JSON shape when a value is supplied.
    cond = f"CASE WHEN json_valid({name}) THEN "
    cond += (f"json_type({name})='{shape}'" if shape else "1")
    cond += " ELSE 0 END"
    return col(name, "TEXT", sql + f"CHECK({name} IS NULL OR ({cond}))", reason)

def fk(fields, target, targets="id", delete="RESTRICT", deferred=False):
    return f"FOREIGN KEY ({fields}) REFERENCES {target}({targets}) ON DELETE {delete}" + (" DEFERRABLE INITIALLY DEFERRED" if deferred else "")
