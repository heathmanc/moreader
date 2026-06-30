"""Tests for config parsing: encapsulators, master, password, save/load."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from moreader.config import (
    DEFAULT_PASSWORD,
    Config,
    ConfigError,
    from_dict,
    load_config,
    save_config,
    to_dict,
)


def test_default_password_is_stored():
    assert Config().security.password == "2134chAP!@" == DEFAULT_PASSWORD


def test_three_encapsulators_and_master_by_default():
    cfg = Config()
    assert len(cfg.plc.encapsulators) == 3
    assert cfg.plc.encapsulators[0].name == "Encapsulator 1"
    assert cfg.plc.master.name == "COS"
    assert cfg.plc.master.mo_verified_tag.name == "MO_Verified"
    assert cfg.plc.master.mo_bypassed_tag.name == "MO_Bypassed"
    assert cfg.plc.master.cycle_stop_tag.name == "System.Mode.CycleStopReq"
    assert cfg.plc.master.heartbeat_tag.name == "Heartbeat"
    assert cfg.plc.heartbeat_interval == 1.0


def test_compare_and_secondary_defaults():
    cfg = Config()
    assert cfg.compare.mo_last_digits == 4
    assert cfg.compare.mo_length == 9
    assert cfg.secondary.enabled is False
    assert cfg.secondary.battery_first_digits == 4
    assert cfg.secondary.battery_min_length == 10


def test_recipe_tag_default_and_description():
    e = Config().plc.encapsulators[0]
    assert e.recipe_tag.name == "recipe[0].Name"
    assert "DINT" in e.recipe_tag.description


def test_from_dict_reads_encapsulators_and_master():
    cfg = from_dict(
        {
            "plc": {
                "encapsulators": [
                    {"name": "Enc A", "ip_address": "10.0.0.5", "slot": 2,
                     "tags": {"recipe_tag": {"name": "recipe[1].Name", "description": "d"}}},
                ],
                "master": {
                    "name": "COS",
                    "ip_address": "10.0.0.1",
                    "tags": {"mo_verified_tag": "MO_OK", "heartbeat_tag": "HB"},
                },
                "heartbeat_interval": 2.5,
            }
        }
    )
    assert len(cfg.plc.encapsulators) == 1
    assert cfg.plc.encapsulators[0].recipe_tag.name == "recipe[1].Name"
    assert cfg.plc.master.ip_address == "10.0.0.1"
    assert cfg.plc.master.mo_verified_tag.name == "MO_OK"
    assert cfg.plc.master.heartbeat_tag.name == "HB"
    assert cfg.plc.heartbeat_interval == 2.5


def test_to_dict_roundtrip_preserves_structure():
    cfg = Config()
    cfg.plc.encapsulators[2].recipe_tag.description = "custom desc"
    cfg.plc.master.mo_verified_tag.name = "Master.MO_Verified"
    cfg2 = from_dict(to_dict(cfg))
    assert cfg2.plc.encapsulators[2].recipe_tag.description == "custom desc"
    assert cfg2.plc.master.mo_verified_tag.name == "Master.MO_Verified"
    assert len(cfg2.plc.encapsulators) == 3


def test_save_and_load_roundtrip(tmp_path):
    cfg = Config()
    cfg.security.password = "secret!"
    cfg.plc.encapsulators[0].ip_address = "172.16.1.20"
    cfg.plc.master.heartbeat_tag.name = "COS.HB"
    cfg.compare.mo_last_digits = 6
    cfg.plc.master.cycle_stop_tag.name = "COS.CycleStop"
    cfg.secondary.enabled = True
    cfg.secondary.battery_min_length = 12
    path = tmp_path / "config.yaml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.security.password == "secret!"
    assert loaded.plc.encapsulators[0].ip_address == "172.16.1.20"
    assert loaded.plc.master.heartbeat_tag.name == "COS.HB"
    assert loaded.plc.master.cycle_stop_tag.name == "COS.CycleStop"
    assert loaded.compare.mo_last_digits == 6
    assert loaded.secondary.enabled is True
    assert loaded.secondary.battery_min_length == 12


def test_invalid_driver_rejected():
    with pytest.raises(ConfigError):
        from_dict({"plc": {"driver": "modbus"}})


def test_stale_config_keys_are_ignored():
    # A config written by an older moreader version must not crash the loader.
    stale = {
        "scanner": {"type": "keyboard", "port": "/dev/ttyACM0", "baudrate": 9600},
        "compare": {"strip": True, "ignore_case": True, "collapse_internal_space": False},
        "shift": {"start_times": ["06:00"], "watch_plc_request": True, "lock_on_startup": True},
        "plc": {"driver": "logix", "machines": [{"name": "old"}]},
    }
    cfg = from_dict(stale)
    assert cfg.scanner.type == "keyboard"
    assert cfg.compare.mo_last_digits == 4         # new default
    assert len(cfg.plc.encapsulators) == 3         # falls back to defaults
    assert cfg.plc.master.name == "COS"
