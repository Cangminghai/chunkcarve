"""Legacy and Minecraft 26.1+ world storage must open through the same project flow."""

import os

import pytest

from mcmap import config, nbt, server


@pytest.fixture(params=["legacy", "namespaced"])
def saved_world(tmp_path, monkeypatch, request):
    world = tmp_path / "instance" / "saves" / "world"
    modern = request.param == "namespaced"
    region = (world / "dimensions" / "minecraft" / "overworld" / "region"
              if modern else world / "region")
    region.mkdir(parents=True)
    (region / "r.0.0.mca").write_bytes(b"\0" * 8192)
    level = world / "level.dat"
    level.touch()
    generator = {
        "seed": 123456789,
        "dimensions": {"minecraft:overworld": {"type": "minecraft:overworld"}},
    }
    data = {
        "LevelName": "Storage test",
        "Version": {"Name": "26.3" if modern else "1.21.1"},
        "DataVersion": 5000 if modern else 3955,
    }
    files = {os.fspath(level): {"Data": data}}
    if modern:
        data["spawn"] = {"pos": [64, 65, -48], "dimension": "minecraft:overworld"}
        generator_file = world / "data" / "minecraft" / "world_gen_settings.dat"
        generator_file.parent.mkdir(parents=True)
        generator_file.touch()
        files[os.fspath(generator_file)] = {"data": generator}
    else:
        data.update(SpawnX=64, SpawnY=65, SpawnZ=-48, WorldGenSettings=generator)
    players = world / "players" / "data" if modern else world / "playerdata"
    players.mkdir(parents=True)
    player_file = players / "00000000-0000-0000-0000-000000000001.dat"
    player_file.touch()
    files[os.fspath(player_file)] = {
        "Pos": [78.0, 65.0, -92.0], "Dimension": "minecraft:overworld",
    }
    monkeypatch.setattr(nbt, "load_file", lambda path: files[os.fspath(path)])
    monkeypatch.setattr(config, "CONFIG_FILE", os.fspath(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PROJECTS_FILE", os.fspath(tmp_path / "projects.json"))
    monkeypatch.setattr(config, "detect_minecraft_dirs", lambda: [])
    for name in ("MCMAP_WORLD", "MCMAP_VANILLA_JAR", "MCMAP_JAR",
                 "MCMAP_MODS_DIR", "MCMAP_MODS"):
        monkeypatch.delenv(name, raising=False)
    return world, region


def test_save_discovery_counts_the_correct_regions(saved_world):
    world, region = saved_world

    assert config.find_world_dir(os.fspath(world.parent.parent)) == os.fspath(world)
    result = config.detect_instance_saves_and_assets(os.fspath(world.parent.parent))
    assert len(result["saves"]) == 1
    save = result["saves"][0]
    assert save["path"] == os.path.realpath(world)
    assert save["regions"] == 1
    assert save["seed"] == "123456789"
    assert save["spawn"] == {"x": 64, "y": 65, "z": -48}


def test_open_project_preserves_world_root_and_loads_metadata(saved_world, monkeypatch):
    world, region = saved_world
    for name in ("DATA_DIR", "REGION_DIR", "WORLD", "_VANILLA_JAR", "_MODS_DIR", "_assets_cache"):
        monkeypatch.setattr(server, name, getattr(server, name))
    monkeypatch.setattr(server, "_init_vanilla", lambda **kwargs: None)
    monkeypatch.setattr(server, "_VANILLA_JAR", "")
    monkeypatch.setattr(server, "_MODS_DIR", "")

    result = server.open_project({"id": "storage-test", "world": os.fspath(world)})

    assert result["ok"] is True
    assert server.DATA_DIR == os.fspath(world)
    assert result["world"]["regionDir"] == os.fspath(region)
    assert result["world"]["regions"] == 1
    assert result["world"]["seed"] == "123456789"
    assert result["world"]["spawn"] == {"x": 64, "y": 65, "z": -48}
    assert result["world"]["players"][0]["x"] == 78
    assert result["world"]["players"][0]["z"] == -92
    assert config.read_config_file()["world"] == os.fspath(world)
    assert server.list_worlds()[0]["path"] == os.path.realpath(world)


def test_migrated_world_prefers_namespaced_regions(tmp_path):
    (tmp_path / "region").mkdir()
    modern = tmp_path / "dimensions" / "minecraft" / "overworld" / "region"
    modern.mkdir(parents=True)

    assert config.world_region_dir(os.fspath(tmp_path)) == os.fspath(modern)
