"""
Project Kessler — Ground AI Node (gnd_ops)
Autonomous Operations Intelligence Desk (AOID)
-------------------------------------------------------------------------------
Pre-conjunction ephemeris propagation, multi-orbit observation window scheduling,
solar umbra eclipse detection, space-weather ingestion, atmospheric drag ML,
and Refined CDM publication / edge uplink.
"""

def __getattr__(name):
    import gnd_ops.ground_ai_node as gn
    if hasattr(gn, name):
        return getattr(gn, name)
    raise AttributeError(f"module 'gnd_ops' has no attribute '{name}'")

__all__ = [
    "CONFIG",
    "GroundAINode",
    "EphemerisProcessor",
    "DataIngestor",
    "FeatureBuilder",
    "DragPredictor",
    "CDMBuilder",
    "Publisher",
    "calculate_sun_position_eci",
]
