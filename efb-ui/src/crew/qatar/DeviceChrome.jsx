/**
 * DeviceChrome — the functional tablet status cluster (Surface-class).
 *
 * Replaces the decorative icon row that used to sit in the top bar
 * (`Sun` / `RefreshCw` / `MoreVertical` with no handlers, plus a hard-coded
 * "97" battery). Everything here is wired to real state through
 * DeviceChromeContext:
 *
 *   HOME        → shell navigation, on every screen, keyboard reachable
 *   BRIGHTNESS  → perceived-brightness dim layer, persisted
 *   REFRESH     → the CURRENT screen's re-fetch handler, with honest outcome
 *   KEBAB       → quick settings: airplane mode, Do Not Disturb, Settings, About
 *   SIGNAL/WIFI → navigator.onLine + navigator.connection, or UNKNOWN
 *   BATTERY     → navigator.getBattery(), or UNKNOWN (never a fake number)
 *
 * Popovers are dismissed by an in-markup backdrop (outside click) and by
 * Escape, and the menu is arrow-key navigable — no document-level listeners,
 * so the behavior is testable without a DOM.
 */

// eslint-disable-next-line no-unused-vars -- classic JSX transform in component tests.
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  House as HomeIcon,
  Sun,
  SunDim,
  RefreshCw,
  EllipsisVertical,
  Plane,
  Bell,
  BellOff,
  Settings as SettingsIcon,
  Info,
  Signal,
  SignalZero,
  SignalLow,
  SignalMedium,
  Wifi,
  WifiOff,
  Battery,
  BatteryCharging,
  BatteryWarning,
  Check,
  X,
} from "lucide-react";
import { BRIGHTNESS_MAX, BRIGHTNESS_MIN, BRIGHTNESS_STEP } from "./deviceChrome.js";
import { useDeviceChrome } from "./useDeviceChrome.jsx";

const APP_VERSION = "QR SmartOps EFB — device chrome v1";

/* ── brightness ─────────────────────────────────────────────────────── */

function BrightnessControl({ brightness, onChange }) {
  const [open, setOpen] = useState(false);
  const step = useCallback(
    (delta) => onChange(Math.min(BRIGHTNESS_MAX, Math.max(BRIGHTNESS_MIN, brightness + delta))),
    [brightness, onChange]
  );
  return (
    <div
      className="qr-qs"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          setOpen(false);
        }
      }}
    >
      <button
        type="button"
        className={`qr-iconbtn qr-chromebtn ${open ? "qr-chromebtn--on" : ""}`}
        aria-label={`Brightness ${brightness} percent`}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={`Display brightness — ${brightness}%`}
        onClick={() => setOpen((v) => !v)}
      >
        {brightness >= 70 ? <Sun size={18} /> : <SunDim size={18} />}
      </button>
      {open && (
        <>
          <div
            className="qr-qs__backdrop"
            data-testid="brightness-backdrop"
            onMouseDown={() => setOpen(false)}
          />
          <div className="qr-qs__pop qr-qs__pop--bright" role="dialog" aria-label="Brightness">
            <div className="qr-qs__poptitle">
              BRIGHTNESS <span className="mono">{brightness}%</span>
            </div>
            <div className="qr-qs__brightrow">
              <button
                type="button"
                className="qr-iconbtn"
                aria-label="Decrease brightness"
                onClick={() => step(-BRIGHTNESS_STEP)}
              >
                <SunDim size={16} />
              </button>
              <input
                type="range"
                className="qr-qs__slider"
                aria-label="Display brightness"
                min={BRIGHTNESS_MIN}
                max={BRIGHTNESS_MAX}
                step={BRIGHTNESS_STEP}
                value={brightness}
                onChange={(e) => onChange(Number(e.target.value))}
              />
              <button
                type="button"
                className="qr-iconbtn"
                aria-label="Increase brightness"
                onClick={() => step(BRIGHTNESS_STEP)}
              >
                <Sun size={16} />
              </button>
            </div>
            <div className="qr-qs__note">
              Dims the EFB display only — the device backlight is not controllable from a browser.
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/* ── quick settings (kebab) ─────────────────────────────────────────── */

function QuickSettings({
  airplaneMode,
  notificationsOff,
  suppressedCount,
  onAirplaneMode,
  onNotificationsOff,
  onOpenSettings,
  appVersion,
}) {
  const [open, setOpen] = useState(false);
  const [focusIdx, setFocusIdx] = useState(0);
  const triggerRef = useRef(null);
  const itemRefs = useRef([]);

  const items = [
    {
      id: "airplane",
      role: "menuitemcheckbox",
      checked: airplaneMode,
      icon: Plane,
      label: "Airplane mode",
      sub: airplaneMode ? "Outbound polling suspended" : "Suspends all outbound polling",
      onSelect: () => onAirplaneMode(!airplaneMode),
      keepOpen: true,
    },
    {
      id: "dnd",
      role: "menuitemcheckbox",
      checked: notificationsOff,
      icon: notificationsOff ? BellOff : Bell,
      label: "Notifications off",
      sub: notificationsOff
        ? `In-app alerts suppressed${suppressedCount ? ` — ${suppressedCount} hidden` : ""}`
        : "Do Not Disturb — hide in-app alerts",
      onSelect: () => onNotificationsOff(!notificationsOff),
      keepOpen: true,
    },
    {
      id: "settings",
      role: "menuitem",
      icon: SettingsIcon,
      label: "Settings",
      sub: "SimBrief and Navigraph setup",
      onSelect: () => onOpenSettings?.(),
      disabled: !onOpenSettings,
    },
    {
      id: "about",
      role: "menuitem",
      icon: Info,
      label: "About",
      sub: appVersion || APP_VERSION,
      onSelect: () => {},
    },
  ];

  const close = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus?.();
  }, []);

  useEffect(() => {
    if (!open) return;
    itemRefs.current[focusIdx]?.focus?.();
  }, [open, focusIdx]);

  const onKeyDown = (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
      return;
    }
    if (!open) return;
    if (e.key === "ArrowDown") {
      e.preventDefault?.();
      setFocusIdx((i) => (i + 1) % items.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault?.();
      setFocusIdx((i) => (i - 1 + items.length) % items.length);
    } else if (e.key === "Home") {
      e.preventDefault?.();
      setFocusIdx(0);
    } else if (e.key === "End") {
      e.preventDefault?.();
      setFocusIdx(items.length - 1);
    }
  };

  return (
    <div className="qr-qs" onKeyDown={onKeyDown}>
      <button
        type="button"
        ref={triggerRef}
        className={`qr-iconbtn qr-chromebtn ${open ? "qr-chromebtn--on" : ""}`}
        aria-label="Quick settings"
        aria-expanded={open}
        aria-haspopup="menu"
        title="Quick settings"
        onClick={() => {
          setFocusIdx(0);
          setOpen((v) => !v);
        }}
      >
        <EllipsisVertical size={18} />
      </button>
      {open && (
        <>
          <div
            className="qr-qs__backdrop"
            data-testid="quicksettings-backdrop"
            onMouseDown={() => setOpen(false)}
          />
          <div className="qr-qs__pop qr-qs__pop--menu" role="menu" aria-label="Quick settings">
            {items.map((item, idx) => (
              <button
                type="button"
                key={item.id}
                ref={(el) => {
                  itemRefs.current[idx] = el;
                }}
                className={`qr-qs__item ${item.checked ? "qr-qs__item--on" : ""}`}
                role={item.role}
                aria-checked={item.role === "menuitemcheckbox" ? Boolean(item.checked) : undefined}
                disabled={item.disabled || undefined}
                onFocus={() => setFocusIdx(idx)}
                onClick={() => {
                  item.onSelect();
                  if (!item.keepOpen) close();
                }}
              >
                <item.icon size={16} />
                <span className="qr-qs__itemtext">
                  <span className="qr-qs__itemlabel">{item.label}</span>
                  <span className="qr-qs__itemsub">{item.sub}</span>
                </span>
                {item.role === "menuitemcheckbox" && (
                  <span className="qr-qs__state mono">
                    {item.checked ? <Check size={14} /> : <X size={14} />}
                    {item.checked ? "ON" : "OFF"}
                  </span>
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/* ── indicators ─────────────────────────────────────────────────────── */

/* Static glyph resolution: the react-hooks/static-components rule rejects a
   component that comes from a function call during render, but accepts a
   ternary of static identifiers — so both indicators resolve inline. */
function NetworkIndicators({ network }) {
  const SignalGlyph =
    network.bars == null
      ? Signal
      : network.bars <= 0
        ? SignalZero
        : network.bars === 1
          ? SignalLow
          : network.bars === 2
            ? SignalMedium
            : Signal;
  const WifiGlyph = network.wifi ? Wifi : WifiOff;
  return (
    <span
      className={`qr-netind qr-netind--${network.mode}`}
      title={network.title}
      data-testid="network-indicator"
      role="status"
    >
      <SignalGlyph size={15} />
      <WifiGlyph size={15} />
      <span className="qr-netind__label">{network.label}</span>
    </span>
  );
}

function BatteryIndicator({ battery }) {
  const Glyph = !battery.known ? BatteryWarning : battery.charging ? BatteryCharging : Battery;
  return (
    <span
      className={`qr-batt ${battery.known ? "" : "qr-batt--unknown"}`}
      title={battery.title}
      data-testid="battery-indicator"
      role="status"
    >
      <span className="mono qr-batt__value">{battery.label}</span>
      <Glyph size={16} className="qr-batt__icon" />
    </span>
  );
}

/* ── the cluster ────────────────────────────────────────────────────── */

/**
 * The persistent device chrome. Rendered by TopHeader, so it appears on
 * every screen of the EFB without each screen having to opt in.
 */
export function DeviceStatusCluster() {
  const {
    prefs,
    battery,
    network,
    goHome,
    goToSettings,
    refresh,
    refreshState,
    setAirplaneMode,
    setBrightness,
    setNotificationsOff,
    suppressedCount,
    appVersion,
  } = useDeviceChrome();

  const busy = Boolean(refreshState?.busy);

  return (
    <span className="qr-chrome" data-testid="device-chrome">
      <button
        type="button"
        className="qr-iconbtn qr-chromebtn qr-chromebtn--home"
        aria-label="Home"
        title="Home"
        onClick={() => goHome?.()}
      >
        <HomeIcon size={18} />
        <span className="qr-chromebtn__text">HOME</span>
      </button>

      <BrightnessControl brightness={prefs.brightness} onChange={setBrightness} />

      <button
        type="button"
        className="qr-iconbtn qr-chromebtn"
        aria-label="Refresh this screen"
        aria-busy={busy || undefined}
        title={
          refreshState && !refreshState.busy && refreshState.text
            ? refreshState.text
            : "Re-fetch the live data on this screen"
        }
        disabled={busy || undefined}
        onClick={() => refresh?.()}
      >
        <RefreshCw size={18} className={busy ? "qr-spin" : undefined} />
      </button>

      <QuickSettings
        airplaneMode={prefs.airplaneMode}
        notificationsOff={prefs.notificationsOff}
        suppressedCount={suppressedCount}
        onAirplaneMode={setAirplaneMode}
        onNotificationsOff={setNotificationsOff}
        onOpenSettings={goToSettings ? () => goToSettings() : null}
        appVersion={appVersion}
      />

      {prefs.notificationsOff && (
        <span className="qr-chrome__dnd" title="Notifications are off — in-app alerts are hidden.">
          <BellOff size={14} />
        </span>
      )}

      <NetworkIndicators network={network} />
      <BatteryIndicator battery={battery} />
    </span>
  );
}

/**
 * Corner stack of in-app alerts. Nothing is rendered while Do Not Disturb is
 * on — instead the suppression is stated, so the crew always knows alerts are
 * being withheld.
 */
export function NoticeHost() {
  const { notices, prefs, suppressedCount, dismissNotice } = useDeviceChrome();

  if (prefs.notificationsOff) {
    if (!suppressedCount) return null;
    return (
      <div className="qr-notices" data-testid="notice-host">
        <div className="qr-notice-card qr-notice-card--muted" role="status">
          <BellOff size={14} />
          <span>
            Notifications off — {suppressedCount} alert{suppressedCount === 1 ? "" : "s"} suppressed.
          </span>
        </div>
      </div>
    );
  }

  if (!notices.length) return null;

  return (
    <div className="qr-notices" data-testid="notice-host">
      {notices.map((n) => (
        <div
          key={n.id}
          className={`qr-notice-card qr-notice-card--${n.kind}`}
          role={n.kind === "error" ? "alert" : "status"}
        >
          <span className="qr-notice-card__text">{n.text}</span>
          <button
            type="button"
            className="qr-iconbtn qr-notice-card__x"
            aria-label="Dismiss alert"
            onClick={() => dismissNotice(n.id)}
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}

export { APP_VERSION };
