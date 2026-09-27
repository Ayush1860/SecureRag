"""Deployment assets: demo key generation and the CloudFormation template's safety properties."""
import json
from pathlib import Path

import pytest
import yaml

from scripts.create_demo_keys import build
from securerag.config import Settings
from securerag.security.auth import Authenticator

ROOT = Path(__file__).resolve().parent.parent


def test_demo_keys_authenticate_as_their_roles(tmp_path):
    plain, hashed = build()
    assert set(plain) == {"guest", "employee", "exec"}
    assert all(key not in json.dumps(hashed) for key in plain.values())  # only hashes are stored
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(json.dumps(hashed), encoding="utf-8")
    auth = Authenticator(Settings(auth_mode="api_key", api_keys_file=str(keys_file)))
    for role, key in plain.items():
        assert auth.authenticate({"X-API-Key": key}).role == role


class _CfnLoader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node, deep=True)}
    return {suffix: loader.construct_mapping(node, deep=True)}


_CfnLoader.add_multi_constructor("!", _tag)


@pytest.fixture(scope="module")
def template():
    return yaml.load((ROOT / "deploy/aws/backend.yaml").read_text(encoding="utf-8"), Loader=_CfnLoader)


def test_template_cost_and_abuse_caps(template):
    fn = template["Resources"]["Function"]["Properties"]
    params = template["Parameters"]["ReservedConcurrency"]
    assert params["Default"] == 2 and params["MaxValue"] <= 2  # cap can never be raised past 2
    assert fn["ReservedConcurrentExecutions"]["If"][1] == {"Ref": "ReservedConcurrency"}
    assert fn["EphemeralStorage"]["Size"] == 1024 and fn["Timeout"] == 60
    assert template["Resources"]["LogGroup"]["Properties"]["RetentionInDays"] == 7
    lifecycle = json.loads(template["Resources"]["Repository"]["Properties"]["LifecyclePolicy"]["LifecyclePolicyText"])
    assert lifecycle["rules"][0]["selection"]["countNumber"] == 2
    types = {r["Type"] for r in template["Resources"].values()}
    assert not types & {"AWS::EC2::NatGateway", "AWS::EC2::Instance", "AWS::Lambda::Version"}


def test_template_has_no_secret_values_and_scoped_trust(template):
    env = template["Resources"]["Function"]["Properties"]["Environment"]["Variables"]
    assert not any(k in env for k in ("SECURERAG_AES_KEY_B64", "GROQ_API_KEY", "API_KEYS_JSON"))
    trust = template["Resources"]["DeployRole"]["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    cond = trust["Condition"]["StringEquals"]
    assert cond["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    subs = cond["token.actions.githubusercontent.com:sub"]
    assert len(subs) == 2 and all("ref:refs/heads/" in json.dumps(s) and "*" not in json.dumps(s) for s in subs)
    url_cors = template["Resources"]["FunctionUrl"]["Properties"]["Cors"]
    assert "*" not in json.dumps(url_cors["AllowOrigins"])


def test_only_auth_token_actions_use_wildcard_resource(template):
    wildcard_actions = []
    for name in ("ExecutionRole", "DeployRole"):
        for policy in template["Resources"][name]["Properties"]["Policies"]:
            for stmt in policy["PolicyDocument"]["Statement"]:
                if stmt["Resource"] == "*":
                    wildcard_actions.append(stmt["Action"])
                actions = stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
                assert not any(a.endswith(":*") or a == "*" for a in actions)
    assert wildcard_actions == ["ecr:GetAuthorizationToken",
                                ["ecr-public:GetAuthorizationToken", "sts:GetServiceBearerToken"]]
