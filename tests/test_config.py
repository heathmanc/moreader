"""Tests for config parsing, machines, tag descriptions, password, save/load."""

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


def test_three_machines_by_default():
    cfg = Config()
    assert len(cfg.plc.machines) == 3
    assert cfg.plc.machines[0].name == "Machine 1"
    assert cfg.plc.machines[0].ip_address == "192.168.1.11"


def test_model_tag_default_and_description():
    m = Config().plc.machines[0]
    assert m.model_tag.name == "recipe[0].Name"
    assert "DINT" in m.model_tag.description


def test_from_dict_reads_machine_tags():
    cfg = from_dict(
        {
            "plc": {
                "machines": [
                    {
                        "name": "Press A",
                        "ip_address": "10.0.0.5",
                        "slot": 2,
                        "tags": {
                            "model_tag": {"name": "recipe[1].Name", "description": "the model"},
                            "run_permit_tag": "PermitA",
                        },
                    }
                ]
            }
        }
    )
    assert len(cfg.plc.machines) == 1
    m = cfg.plc.machines[0]
    assert m.name == "Press A"
    assert m.ip_address == "10.0.0.5"
    assert m.slot == 2
    assert m.model_tag.name == "recipe[1].Name"
    assert m.run_permit_tag.name == "PermitA"


def test_compare_defaults_last_four():
    cfg = Config()
    assert cfg.compare.mo_last_digits == 4
    assert cfg.compare.digits_only is True


def test_to_dict_roundtrip_preserves_machines():
    cfg = Config()
    cfg.plc.machines[2].model_tag.description = "custom desc"
    cfg2 = from_dict(to_dict(cfg))
    assert cfg2.plc.machines[2].model_tag.description == "custom desc"
    assert cfg2.security.password == cfg.security.password
    assert len(cfg2.plc.machines) == 3


def test_save_and_load_roundtrip(tmp_path):
    cfg = Config()
    cfg.security.password = "secret!"
    cfg.plc.machines[0].ip_address = "172.16.1.20"
    cfg.plc.machines[0].run_permit_tag.name = "Line1.Permit"
    cfg.compare.mo_last_digits = 6
    path = tmp_path / "config.yaml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.security.password == "secret!"
    assert loaded.plc.machines[0].ip_address == "172.16.1.20"
    assert loaded.plc.machines[0].run_permit_tag.name == "Line1.Permit"
    assert loaded.compare.mo_last_digits == 6


def test_invalid_driver_rejected():
    with pytest.raises(ConfigError):
        from_dict({"plc": {"driver": "modbus"}})


def test_stale_config_keys_are_ignored():
    # A config written by an older moreader version must not crash the loader.
    stale = {
        "scanner": {"type": "keyboard", "port": "/dev/ttyACM0", "baudrate": 9600},
        "compare": {"strip": True, "ignore_case": True, "collapse_internal_space": False},
        "plc": {"driver": "logix", "ip_address": "192.168.1.10", "tags": {"expected_model": "X"}},
    }
    cfg = from_dict(stale)
    assert cfg.scanner.type == "keyboard"
    assert cfg.compare.mo_last_digits == 4   # falls back to the new default
    assert len(cfg.plc.machines) == 3
