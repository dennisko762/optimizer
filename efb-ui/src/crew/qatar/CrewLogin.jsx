/**
 * CrewLogin — fullscreen VA crew login gate (M2b, Qatar maroon).
 *
 * Shown on every app start until a crew session exists. Mock auth: any
 * non-empty pilot ID + password is accepted (crewAuth.login); the session
 * is persisted to localStorage and survives restarts. Real VA auth plugs
 * into crewAuth.js later.
 */

import { useState } from "react";
import { LogIn, ShieldCheck } from "lucide-react";
import { login, saveSession } from "../crewAuth.js";

export default function CrewLogin({ session, onLoggedIn }) {
  const [pilotId, setPilotId] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState([]);

  function submit(e) {
    e.preventDefault();
    const result = login({ pilotId, password });
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }
    setErrors([]);
    const session = saveSession(result.session);
    onLoggedIn?.(session);
  }

  return (
    <div className="qr-login">
      <div className="qr-login__brand">
        <div className="qr-login__logo">
          <span className="qr-logo__word">QATAR</span>
          <span className="qr-logo__oryx" aria-hidden="true">
            <svg viewBox="0 0 64 32">
              <path
                d="M2 22 C 14 10, 30 4, 60 6 C 42 12, 34 16, 28 22 C 24 26, 16 27, 10 25 C 6 24, 4 23, 2 22 Z"
                fill="currentColor"
                opacity="0.9"
              />
              <path d="M60 6 L 48 10" stroke="currentColor" strokeWidth="1.5" fill="none" />
            </svg>
          </span>
          <span className="qr-logo__air">AIRWAYS</span>
        </div>
        <div className="qr-login__title">CREW LOGIN</div>
        <div className="qr-login__sub">Virtual Airline Crew Access</div>
      </div>

      <form className="qr-login__card" onSubmit={submit}>
        <div className="qr-login__pilot">
          <span className="qr-label">PILOT</span>
          <span className="mono">{session?.pilotId || "—"}</span>
        </div>

        <label className="qr-login__field">
          <span className="qr-label">PILOT ID / PIN</span>
          <input
            className="qr-login__input mono"
            value={pilotId}
            onChange={(e) => setPilotId(e.target.value)}
            placeholder="e.g. QR-1234 or 0042"
            autoComplete="off"
            autoFocus
          />
        </label>

        <label className="qr-login__field">
          <span className="qr-label">VA PASSWORD</span>
          <input
            className="qr-login__input mono"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            autoComplete="off"
          />
        </label>

        {errors.length > 0 && (
          <div className="qr-login__errors">
            {errors.map((msg) => (
              <div key={msg}>{msg}</div>
            ))}
          </div>
        )}

        <button className="qr-goldbtn qr-login__btn" type="submit">
          <LogIn size={15} /> SIGN IN
        </button>

        <div className="qr-login__hint">
          <ShieldCheck size={13} />
          <span>Mock sign-in — any pilot ID and password is accepted for now.</span>
        </div>
      </form>
    </div>
  );
}
