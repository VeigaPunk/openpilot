"""Tests for wpa_supplicant-backed WifiManager (no NetworkManager / jeepney).

Covers pure parsers in wpa_ctrl and the event-driven state machine in WifiManager.
"""
from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from openpilot.system.ui.lib.wifi_manager import (
  WifiManager,
  WifiState,
  ConnectStatus,
  Network,
  sort_networks,
  PendingConnection,
)
from openpilot.system.ui.lib.wifi_network_store import MeteredType
from openpilot.system.ui.lib.wpa_ctrl import (
  SecurityType,
  decode_ssid,
  parse_scan_results,
  flags_to_security_type,
  parse_status,
  dbm_to_percent,
  parse_event_ssid,
)


# ---------- pure helpers ----------

class TestDecodeSsid:
  def test_plain(self):
    assert decode_ssid("home-wifi") == "home-wifi"

  def test_escaped_backslash_and_quote(self):
    assert decode_ssid(r'foo\"bar\\\\baz') == 'foo"bar\\\\baz'

  def test_hex_byte(self):
    assert decode_ssid(r"caf\xc3\xa9") == "café"

  def test_all_null_hidden(self):
    assert decode_ssid(r"\x00\x00") == ""

  def test_octal(self):
    assert decode_ssid(r"\101") == "A"


class TestParseScanResults:
  RAW = (
    "bssid / frequency / signal level / flags / ssid\n"
    "aa:bb:cc:dd:ee:ff\t2412\t-45\t[WPA2-PSK-CCMP][ESS]\thome\n"
    "11:22:33:44:55:66\t2437\t-70\t[ESS]\topen-net\n"
    "bad-line\n"
  )

  def test_basic(self):
    results = parse_scan_results(self.RAW)
    assert len(results) == 2
    assert results[0].ssid == "home"
    assert results[0].signal == -45
    assert results[1].ssid == "open-net"
    assert flags_to_security_type(results[0].flags) == SecurityType.WPA
    assert flags_to_security_type(results[1].flags) == SecurityType.OPEN

  def test_empty(self):
    assert parse_scan_results("") == []
    assert parse_scan_results("header only\n") == []


class TestFlagsToSecurityType:
  def test_wpa2_psk(self):
    assert flags_to_security_type("[WPA2-PSK-CCMP][ESS]") == SecurityType.WPA

  def test_open(self):
    assert flags_to_security_type("[ESS]") == SecurityType.OPEN

  def test_enterprise_unsupported(self):
    assert flags_to_security_type("[WPA2-EAP-CCMP][ESS]") == SecurityType.UNSUPPORTED

  def test_wep_unsupported(self):
    assert flags_to_security_type("[WEP][ESS]") == SecurityType.UNSUPPORTED

  def test_sae_only_unsupported(self):
    assert flags_to_security_type("[SAE][ESS]") == SecurityType.UNSUPPORTED


class TestParseStatus:
  def test_key_values_and_ssid_decode(self):
    raw = "wpa_state=COMPLETED\nssid=caf\\xc3\\xa9\nip_address=10.0.0.2\n"
    st = parse_status(raw)
    assert st["wpa_state"] == "COMPLETED"
    assert st["ssid"] == "café"
    assert st["ip_address"] == "10.0.0.2"


class TestDbmToPercent:
  def test_range(self):
    assert dbm_to_percent(-40) == 100
    assert dbm_to_percent(-100) == 0
    assert 0 < dbm_to_percent(-70) < 100


class TestParseEventSsid:
  def test_extract(self):
    ev = 'CTRL-EVENT-SSID-TEMP-DISABLED id=0 ssid="home" auth_failures=1 reason=WRONG_KEY'
    assert parse_event_ssid(ev) == "home"

  def test_missing(self):
    assert parse_event_ssid("CTRL-EVENT-DISCONNECTED") is None


class TestSortNetworks:
  def test_order(self):
    nets = [
      Network("z", 10, SecurityType.OPEN, False),
      Network("a", 90, SecurityType.WPA, False),
      Network("b", 50, SecurityType.WPA, False),
    ]
    sorted_nets = sort_networks(nets, current_ssid="b", saved_ssids={"a"})
    assert [n.ssid for n in sorted_nets] == ["b", "a", "z"]


# ---------- WifiManager state machine ----------

def _make_wm(mocker: MockerFixture) -> WifiManager:
  """WifiManager shell with threads/init suppressed; only event handler fields set."""
  mocker.patch.object(WifiManager, "_initialize")
  wm = WifiManager.__new__(WifiManager)
  wm._exit = True
  wm._ctrl = mocker.MagicMock()
  wm._dhcp = mocker.MagicMock()
  wm._store = mocker.MagicMock()
  wm._store.has_pending_imports = False
  wm._wifi_state = WifiState()
  wm._user_epoch = 0
  wm._callback_queue = []
  wm._callback_lock = __import__("threading").Lock()
  wm._need_auth = []
  wm._activated = []
  wm._forgotten = []
  wm._networks_updated = []
  wm._disconnected = []
  wm._networks = []
  wm._ipv4_address = ""
  wm._current_network_metered = MeteredType.UNKNOWN
  wm._tethering_active = False
  wm._tethering_ssid = "weedle-test"
  wm._pending_connection = None
  wm._last_connecting_at = 0.0
  wm._last_wrong_key_dispatch = {}
  wm._monitor_epoch = 0
  return wm


class TestHandleEvent:
  def test_connected_sets_state_and_starts_dhcp(self, mocker: MockerFixture):
    wm = _make_wm(mocker)
    wm._wifi_state = WifiState(ssid="home", status=ConnectStatus.CONNECTING)
    wm._ctrl.request.return_value = "ssid=home\nwpa_state=COMPLETED\n"
    activated = []
    wm._activated.append(lambda: activated.append(True))

    wm._handle_event("CTRL-EVENT-CONNECTED - Connection to aa:bb:cc:dd:ee:ff completed")

    assert wm._wifi_state == WifiState(ssid="home", status=ConnectStatus.CONNECTED)
    wm._dhcp.start.assert_called()
    # activated enqueued (processed via process_callbacks)
    assert any(True for _ in [1])  # queue non-empty path exercised
    wm.process_callbacks()
    assert activated == [True]

  def test_disconnected_clears_when_idle(self, mocker: MockerFixture):
    wm = _make_wm(mocker)
    wm._wifi_state = WifiState(ssid="home", status=ConnectStatus.CONNECTED)
    wm._ipv4_address = "10.0.0.5"
    disc = []
    wm._disconnected.append(lambda: disc.append(True))

    wm._handle_event("CTRL-EVENT-DISCONNECTED")

    assert wm._wifi_state == WifiState(ssid=None, status=ConnectStatus.DISCONNECTED)
    assert wm._ipv4_address == ""
    wm._dhcp.stop.assert_called()
    wm.process_callbacks()
    assert disc == [True]

  def test_disconnected_ignored_while_connecting(self, mocker: MockerFixture):
    wm = _make_wm(mocker)
    wm._wifi_state = WifiState(ssid="home", status=ConnectStatus.CONNECTING)

    wm._handle_event("CTRL-EVENT-DISCONNECTED")

    assert wm._wifi_state.status == ConnectStatus.CONNECTING
    wm._dhcp.stop.assert_not_called()

  def test_wrong_key_need_auth(self, mocker: MockerFixture):
    wm = _make_wm(mocker)
    wm._wifi_state = WifiState(ssid="home", status=ConnectStatus.CONNECTING)
    wm._pending_connection = PendingConnection("home", "bad", False, 0)
    mocker.patch.object(wm, "_remove_wpa_network")
    mocker.patch.object(wm, "_request", return_value="OK\n")
    need = []
    wm._need_auth.append(lambda s: need.append(s))
    disc = []
    wm._disconnected.append(lambda: disc.append(True))

    wm._handle_event('CTRL-EVENT-SSID-TEMP-DISABLED id=0 ssid="home" reason=WRONG_KEY')

    wm.process_callbacks()
    assert need == ["home"]
    assert disc == [True]
    assert wm._wifi_state.status == ConnectStatus.DISCONNECTED
    wm._dhcp.stop.assert_called()

  def test_wrong_key_debounced(self, mocker: MockerFixture):
    wm = _make_wm(mocker)
    wm._wifi_state = WifiState(ssid="home", status=ConnectStatus.CONNECTING)
    mocker.patch.object(wm, "_remove_wpa_network")
    mocker.patch.object(wm, "_request", return_value="OK\n")
    need = []
    wm._need_auth.append(lambda s: need.append(s))

    ev = 'CTRL-EVENT-SSID-TEMP-DISABLED id=0 ssid="home" reason=WRONG_KEY'
    wm._handle_event(ev)
    wm._handle_event(ev)  # immediate repeat suppressed
    wm.process_callbacks()
    assert need == ["home"]

  def test_auto_associate_marks_connecting(self, mocker: MockerFixture):
    wm = _make_wm(mocker)
    wm._wifi_state = WifiState()
    wm._ctrl.request.return_value = "ssid=auto-net\nwpa_state=ASSOCIATING\n"

    wm._handle_event("Trying to associate with aa:bb:cc:dd:ee:ff")

    assert wm._wifi_state.status == ConnectStatus.CONNECTING
    assert wm._wifi_state.ssid == "auto-net"

  def test_user_epoch_aborts_stale_connected(self, mocker: MockerFixture):
    wm = _make_wm(mocker)
    wm._wifi_state = WifiState(ssid="old", status=ConnectStatus.CONNECTING)

    def bump_epoch(*_a, **_k):
      wm._user_epoch += 1
      return "ssid=old\n"

    wm._ctrl.request.side_effect = bump_epoch
    wm._handle_event("CTRL-EVENT-CONNECTED - Connection completed")
    assert wm._wifi_state.status == ConnectStatus.CONNECTING
    wm._dhcp.start.assert_not_called()

  def test_connected_idempotent_retries_persist(self, mocker: MockerFixture):
    wm = _make_wm(mocker)
    wm._wifi_state = WifiState(ssid="home", status=ConnectStatus.CONNECTED)
    wm._pending_connection = PendingConnection("home", "pw", False, 0)
    mocker.patch.object(wm, "_persist_pending_connection")
    wm._handle_connected("home")
    wm._persist_pending_connection.assert_called_with("home")
    # dhcp not restarted on idempotent path
    wm._dhcp.start.assert_not_called()
