from collections import Counter


def smiles_to_formula(smiles: str) -> str:
    """Convert a SMILES string to a chemical formula."""
    import pysmiles
    import networkx as nx

    mol = pysmiles.read_smiles(smiles, explicit_hydrogen=True)
    counts = Counter(nx.get_node_attributes(mol, "element").values())
    charge_total = sum(nx.get_node_attributes(mol, "charge").values())

    parts = []
    if "C" in counts:
        c = counts.pop("C")
        parts.append(f"C{c if c > 1 else ''}")
    if "H" in counts:
        h = counts.pop("H")
        parts.append(f"H{h if h > 1 else ''}")
    for el in sorted(counts):
        num = counts[el]
        parts.append(f"{el}{num if num > 1 else ''}")

    formula = "".join(parts)
    if charge_total > 0:
        formula += f"{charge_total if charge_total > 1 else ''}+"
    elif charge_total < 0:
        formula += f"{abs(charge_total) if charge_total < -1 else ''}-"
    return formula
