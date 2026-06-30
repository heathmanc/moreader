"""Tests for config parsing, tag descriptions, password, and save/load."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moreader.config import (
    DEFAULT_PASSWORD,
    Config,
    from_dict,
    load_config,
    save_config,
    to_dict,
)


def test_default_password_is_stored():
    cfg = Config()
    assert cfg.security.password == "2134chAP!@" == DEFAULT_PASSWORD


def test_tags_have_name_and_description_defaults():
    cfg = Config()
    assert cfg.plc.expected_model.name
    assert cfg.plc.run_permit.description
    assert "STRING" in cfg.plc.expected_model.description


def test_from_dict_reads_nested_tags():
    cfg = from_dict(
        {
            "plc": {
                "driver": "logix",
                "ip_address": "10.0.0.5",
                "slot": 2,
                "tags": {
                    "expected_model": {"name": "MyModel", "description": "the model"},
                    "run_permit": {"name": "Permit"},
                },
            }
        }
    )
    assert cfg.plc.ip_address == "10.0.0.5"
    assert cfg.plc.slot == 2
    assert cfg.plc.expected_model.name == "MyModel"
    assert cfg.plc.expected_model.description == "the model"
    assert cfg.plc.run_permit.name == "Permit"


def test_bare_string_tag_allowed():
    cfg = from_dict({"plc": {"tags": {"alarm": "MyAlarmTag"}}})
    assert cfg.plc.alarm.name == "MyAlarmTag"


def test_to_dict_roundtrip_preserves_descriptions():
    cfg = Config()
    cfg.plc.expected_model.description = "custom desc"
    cfg2 = from_dict(to_dict(cfg))
    assert cfg2.plc.expected_model.description == "custom desc"
    assert cfg2.security.password == cfg.security.password


def test_save_and_load_roundtrip(tmp_path):
    cfg = Config()
    cfg.security.password = "secret!"
    cfg.plc.ip_address = "172.16.1.20"
    cfg.plc.run_permit.name = "Line1.Permit"
    path = tmp_path / "config.yaml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.security.password == "secret!"
    assert loaded.plc.ip_address == "172.16.1.20"
    assert loaded.plc.run_permit.name == "Line1.Permit"


def test_invalid_driver_rejected():
    import pytest

    from moreader.config import ConfigError

    with pytest.raises(ConfigError):
        from_dict({"plc": {"driver": "modbus"}})
