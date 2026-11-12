"""Decode the palette encodings observed in official Minecraft 26.3 saves."""

import numpy as np
import pytest

from mcmap import anvil, render


@pytest.mark.parametrize("entry,properties", [
    ({"Name": "minecraft:oak_stairs", "Properties": {
        "facing": "east", "half": "top", "shape": "outer_left", "waterlogged": "false"}},
     {"facing": "east", "half": "top", "shape": "outer_left", "waterlogged": "false"}),
    ({"id": "minecraft:oak_stairs", "properties": {
        "facing": "east", "half": "top", "shape": "outer_left", "waterlogged": "false"}},
     {"facing": "east", "half": "top", "shape": "outer_left", "waterlogged": "false"}),
    ("minecraft:oak_stairs",
     {"facing": "north", "half": "bottom", "shape": "straight", "waterlogged": "false"}),
    ({"": "minecraft:oak_stairs"},
     {"facing": "north", "half": "bottom", "shape": "straight", "waterlogged": "false"}),
])
def test_saved_stairs_preserve_name_and_state(entry, properties):
    chunk = anvil.parse_chunk_tree({
        "DataVersion": 5023, "sections": [{"Y": 4, "block_states": {"palette": [entry]}}],
    })
    section = chunk.sections[4]

    assert section.palette == ["minecraft:oak_stairs"]
    assert section.properties == [properties]
    assert np.array_equal(section.blocks, np.zeros(4096, dtype=np.uint16))


@pytest.mark.parametrize("entry", ["minecraft:stone", {"": "minecraft:stone"}])
def test_compact_palette_produces_terrain_instead_of_air(entry):
    chunk = anvil.parse_chunk_tree({
        "DataVersion": 5023, "sections": [{"Y": 4, "block_states": {"palette": [entry]}}],
    })

    height, rgba, water, names = render.chunk_heightmap(chunk)

    assert np.all(height == 79)
    assert np.all(rgba[:, :, 3] == 255)
    assert not water.any()
    assert {render.id_name(int(n)) for n in np.unique(names)} == {"minecraft:stone"}


def test_mixed_palette_keeps_indices_and_default_log_axis():
    # NBT wraps strings in an empty-key compound when a list also contains compounds.
    chunk = anvil.parse_chunk_tree({
        "DataVersion": 5023, "sections": [{"Y": 0, "block_states": {
            "palette": [{"": "minecraft:stone"}, {"": "minecraft:oak_log"},
                        {"id": "minecraft:oak_log", "properties": {"axis": "x"}}],
            "data": [0x210] * 256,
        }}],
    })
    section = chunk.sections[0]

    assert section.palette == ["minecraft:stone", "minecraft:oak_log", "minecraft:oak_log"]
    assert section.properties == [None, {"axis": "y"}, {"axis": "x"}]
    assert section.blocks[:4].tolist() == [0, 1, 2, 0]
