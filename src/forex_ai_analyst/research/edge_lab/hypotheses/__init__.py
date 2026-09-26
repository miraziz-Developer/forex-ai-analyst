"""Pre-registered hypotheses, one module per family. Never edit a registered version."""
from forex_ai_analyst.research.edge_lab.hypotheses import crowding_exhaustion

HYPOTHESES = {m["hypothesis_id"]: m for m in (crowding_exhaustion.MANIFEST_V1, crowding_exhaustion.MANIFEST_V2,
                                                       crowding_exhaustion.MANIFEST_V3)}
