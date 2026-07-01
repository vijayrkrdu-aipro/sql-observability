import pytest
import yaml

from src.config import ConfigError, env_var_prefix, load_config, validate_config

VALID_CONFIG = {
    "repository": {
        "server": "REPO-SQL-01",
        "database": "DBA_Observability",
        "driver": "ODBC Driver 18 for SQL Server",
        "encrypt": True,
        "trust_server_certificate": False,
        "integrated_auth": True,
    },
    "monitored_instances": [
        {
            "name": "PROD-SQL-01",
            "driver": "ODBC Driver 18 for SQL Server",
            "encrypt": True,
            "trust_server_certificate": False,
            "integrated_auth": True,
            "databases": [],
        }
    ],
    "tasks": {
        "cpu": {"enabled": True, "cadence_minutes": 15},
    },
}


def test_validate_config_accepts_valid_config():
    validate_config(VALID_CONFIG)  # no raise


def test_load_config_reads_real_config_yaml():
    # exercises the actual project config.yaml, not just the minimal fixture above
    config = load_config("config.yaml")
    assert config["repository"]["database"] == "DBA_Observability"
    assert any(i["name"] == "PROD-SQL-01" for i in config["monitored_instances"])


def test_load_config_missing_file_raises():
    with pytest.raises(ConfigError, match="not found"):
        load_config("does_not_exist.yaml")


def test_load_config_empty_file_raises(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="empty"):
        load_config(path)


@pytest.mark.parametrize("missing_key", ["repository", "monitored_instances", "tasks"])
def test_validate_config_missing_top_level_key_raises(missing_key):
    config = yaml.safe_load(yaml.safe_dump(VALID_CONFIG))  # deep copy
    del config[missing_key]
    with pytest.raises(ConfigError, match=missing_key):
        validate_config(config)


def test_validate_config_repository_missing_subkey_raises():
    config = yaml.safe_load(yaml.safe_dump(VALID_CONFIG))
    del config["repository"]["database"]
    with pytest.raises(ConfigError, match="repository"):
        validate_config(config)


def test_validate_config_empty_monitored_instances_raises():
    config = yaml.safe_load(yaml.safe_dump(VALID_CONFIG))
    config["monitored_instances"] = []
    with pytest.raises(ConfigError, match="monitored_instances"):
        validate_config(config)


def test_validate_config_duplicate_instance_names_raises():
    config = yaml.safe_load(yaml.safe_dump(VALID_CONFIG))
    config["monitored_instances"].append(dict(config["monitored_instances"][0]))
    with pytest.raises(ConfigError, match="duplicate"):
        validate_config(config)


def test_validate_config_task_missing_cadence_raises():
    config = yaml.safe_load(yaml.safe_dump(VALID_CONFIG))
    del config["tasks"]["cpu"]["cadence_minutes"]
    with pytest.raises(ConfigError, match="cadence_minutes"):
        validate_config(config)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("PROD-SQL-01", "PROD_SQL_01"),
        ("prod.sql.02", "PROD_SQL_02"),
        ("PROD_SQL_03", "PROD_SQL_03"),
    ],
)
def test_env_var_prefix(name, expected):
    assert env_var_prefix(name) == expected
