/**
 * Airline selection screen — displayed before sign-in.
 */

import { useCrewPlatform } from "./useCrewPlatform.js";
import { Plane, LogIn } from "lucide-react";

export default function AirlineSelector() {
  const { providers, selectProvider, selectedProvider, startAuth, loading, error } =
    useCrewPlatform();

  return (
    <div className="airline-selector">
      <div className="airline-selector__header">
        <Plane size={28} />
        <h1>Crew Operations Platform</h1>
        <p>Select your airline to continue</p>
      </div>

      <div className="airline-selector__grid">
        {providers.map((p) => (
          <button
            key={p.id}
            className={`airline-card ${selectedProvider?.id === p.id ? "airline-card--selected" : ""}`}
            onClick={() => selectProvider(p.id)}
            style={{
              "--card-primary": p.theme["--airline-primary"],
              "--card-accent": p.theme["--airline-accent"],
            }}
          >
            <div className="airline-card__name">{p.display_name}</div>
            <div className="airline-card__code">{p.short_code} · {p.icao}</div>
            <div
              className="airline-card__swatch"
              style={{ background: p.theme["--airline-primary"] }}
            />
          </button>
        ))}
      </div>

      {selectedProvider && (
        <div className="airline-selector__auth">
          <p>
            Sign in with vAMSYS as <strong>{selectedProvider.display_name}</strong> pilot.
            <br />
            <small>
              No password is collected by this application. Authentication
              happens through the vAMSYS consent page.
            </small>
          </p>
          <button className="airline-auth-btn" onClick={startAuth} disabled={loading}>
            <LogIn size={16} />
            {loading ? "Starting..." : "Sign in with vAMSYS"}
          </button>
        </div>
      )}

      {error && <div className="airline-error">{error}</div>}
    </div>
  );
}
