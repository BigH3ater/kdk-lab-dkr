"""stacks/wizarr-sync: invitee policy diff logic (no network, no env needed)."""
import importlib.util
from pathlib import Path

STACK = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wizarr_sync", STACK / "sync.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

LDAP = "Jellyfin.Plugin.LDAP_Auth.LdapAuthenticationProviderPlugin"
DEFAULT = "Jellyfin.Server.Implementations.Users.DefaultAuthenticationProvider"


def jellyfin_defaults():
    """What a fresh Wizarr invitee looked like on 2026-11-07 (abridged)."""
    return {
        "IsAdministrator": False,
        "AuthenticationProviderId": DEFAULT,
        "LoginAttemptsBeforeLockout": -1,
        "MaxActiveSessions": 0,
        "EnableSyncTranscoding": True,
        "EnableMediaConversion": True,
        "EnableSharedDeviceControl": True,
        "EnableRemoteAccess": True,
        "EnableContentDownloading": False,
        "EnableContentDeletion": False,
        "EnableRemoteControlOfOtherUsers": False,
        "EnableLiveTvManagement": False,
        "EnablePublicSharing": True,
        "EnableAllFolders": False,
        "EnabledFolders": ["abc"],
    }


def compliant():
    p = jellyfin_defaults()
    p.update(sync.INVITEE_POLICY)
    p["AuthenticationProviderId"] = LDAP
    return p


def test_fresh_invitee_gets_every_changed_field():
    ch = sync.policy_changes(jellyfin_defaults(), LDAP)
    assert ch == {
        "AuthenticationProviderId": (DEFAULT, LDAP),
        "LoginAttemptsBeforeLockout": (-1, 10),
        "MaxActiveSessions": (0, 3),
        "EnableSyncTranscoding": (True, False),
        "EnableMediaConversion": (True, False),
        "EnableSharedDeviceControl": (True, False),
    }


def test_compliant_user_is_idempotent():
    assert sync.policy_changes(compliant(), LDAP) == {}


def test_apply_then_diff_is_empty_and_leaves_other_fields():
    before = jellyfin_defaults()
    snapshot = dict(before)
    after = sync.apply_changes(before, sync.policy_changes(before, LDAP))
    assert before == snapshot  # input not mutated
    assert sync.policy_changes(after, LDAP) == {}
    for k in ("EnablePublicSharing", "EnableAllFolders", "EnabledFolders", "IsAdministrator"):
        assert after[k] == before[k]


def test_admin_is_never_touched():
    p = jellyfin_defaults()
    p["IsAdministrator"] = True
    assert sync.policy_changes(p, LDAP) is None


def test_asserted_defaults_are_put_back_on_drift():
    p = compliant()
    p["EnableContentDownloading"] = True
    p["EnableRemoteAccess"] = False
    assert sync.policy_changes(p, LDAP) == {
        "EnableRemoteAccess": (False, True),
        "EnableContentDownloading": (True, False),
    }


def test_missing_field_and_bool_vs_int_count_as_different():
    p = compliant()
    del p["MaxActiveSessions"]
    p["EnableSyncTranscoding"] = 0  # falsy but not the bool the API expects
    assert sync.policy_changes(p, LDAP) == {
        "MaxActiveSessions": (None, 3),
        "EnableSyncTranscoding": (0, False),
    }


def test_provider_optional_and_policy_scope():
    assert "AuthenticationProviderId" not in sync.policy_changes(jellyfin_defaults())
    assert "IsAdministrator" not in sync.INVITEE_POLICY
    assert "EnablePublicSharing" not in sync.INVITEE_POLICY


def test_none_policy_is_handled():
    ch = sync.policy_changes(None, LDAP)
    assert set(ch) == {"AuthenticationProviderId", *sync.INVITEE_POLICY}
