import { Plane } from "lucide-react";
import "./OptimizerShowcase.css";

const STAGES = [
  {
    id: "01",
    title: "Source Inputs",
    summary: "Collects live aircraft state, planning data and operational triggers before any optimization is attempted.",
    tone: "source",
    blocks: [
      {
        title: "Simulator Telemetry",
        modules: "data_fetcher/sim, sim_bridge",
        bullets: [
          "Altitude, Mach, weight, fuel, wind and remaining distance",
          "Continuous SimConnect polling with reconnect handling",
        ],
      },
      {
        title: "Flight Plan Import",
        modules: "data_fetcher/simbrief",
        bullets: [
          "OFP route, schedule, planned CI / Mach and fuel figures",
          "Destination coordinates and aircraft identification",
        ],
      },
      {
        title: "Operational Triggers",
        modules: "optimizer/operational_data",
        bullets: [
          "Connex uplinks, ATC restrictions, reroutes and weather refreshes",
          "Manual recovery targets and airline / hub inputs",
        ],
      },
    ],
  },
  {
    id: "02",
    title: "Context Build",
    summary: "Transforms raw inputs into the current flight state and the operational context used by the solver.",
    tone: "context",
    blocks: [
      {
        title: "Live Flight State",
        modules: "data_fetcher/sim normalizers",
        bullets: [
          "Normalizes simulator values into a stable internal state",
          "Maintains route distance, groundspeed and fuel context",
        ],
      },
      {
        title: "ETA and Delay Logic",
        modules: "delay_module",
        bullets: [
          "Computes rolling ETA against planned in-block time",
          "Classifies delay status and decides when recalculation is warranted",
        ],
      },
      {
        title: "Scenario Interpretation",
        modules: "optimizer/scenario_engine",
        bullets: [
          "Derives objective, priority and optimization constraints",
          "Applies timing, speed, level, holding and recovery logic",
        ],
      },
    ],
  },
  {
    id: "03",
    title: "Optimization Core",
    summary: "Simulates candidate strategies, converts trade-offs into cost and returns the preferred recommendation.",
    tone: "core",
    blocks: [
      {
        title: "Performance Model",
        modules: "performance_engine",
        bullets: [
          "Evaluates remaining-cruise time and fuel by candidate Mach",
          "Selects the right aircraft-specific performance data",
        ],
      },
      {
        title: "Cost and Strategy Solver",
        modules: "optimizer/cost_model, strategy, cost_optimizer",
        bullets: [
          "Builds the candidate CI / Mach set",
          "Prices fuel, time, ETS and disruption exposure",
          "Returns best strategy, deltas and explanation text",
        ],
      },
      {
        title: "Advanced Trajectory Branch",
        modules: "trajectory_engine",
        optional: true,
        bullets: [
          "Optional climb, cruise and descent optimization path",
          "Published separately for advanced trajectory workflows",
        ],
      },
    ],
  },
  {
    id: "04",
    title: "Delivery Layer",
    summary: "Packages the solver result and exposes it to the pilot-facing interface over the local network.",
    tone: "delivery",
    blocks: [
      {
        title: "Application and API Layer",
        modules: "optimizer/app, optimizer/api",
        bullets: [
          "Combines scenario, ETA and optimization outputs into one response",
          "Publishes optimize, SimBrief and trajectory endpoints",
        ],
      },
      {
        title: "Pilot-Facing EFB",
        modules: "efb-ui",
        bullets: [
          "Recommended CI / Mach and rationale",
          "Strategy table, telemetry status, ETA state and operational prompts",
        ],
      },
    ],
  },
];

const FLOW_STEPS = [
  "Capture aircraft and schedule data",
  "Normalize state and derive ETA",
  "Interpret operational scenario",
  "Simulate and price candidate strategies",
  "Publish recommendation to the EFB",
];

const CROSS_LINKS = [
  "Connex data is resolved before optimization and feeds both the scenario path and the cost path when passenger disruption risk matters.",
  "ETA and delay status loop back into scenario selection whenever the arrival picture changes enough to justify a new calculation.",
  "The trajectory engine is a side branch rather than the default runtime path; it extends the platform with dedicated advanced endpoints.",
];

const OUTPUTS = [
  "Recommended Cost Index and target Mach",
  "Fuel, time and economic delta versus the current strategy",
  "Operational rationale, priority and applicable constraints",
  "Candidate strategy table for comparison",
  "Live ETA, delay band and telemetry health status",
];

const DEPLOYMENT_NOTES = [
  "Runs as a local executable on the simulator PC.",
  "Uses SimConnect for live aircraft state and SimBrief for OFP sync.",
  "Serves browser clients over the local network or remote access overlay.",
];

function StageBlock({ block, tone }) {
  return (
    <article className={`stage-block stage-block--${tone} ${block.optional ? "stage-block--optional" : ""}`.trim()}>
      <div className="stage-block__title-row">
        <h3>{block.title}</h3>
        {block.optional && <span className="stage-block__flag">Optional</span>}
      </div>
      <div className="stage-block__modules">{block.modules}</div>
      <ul className="stage-block__list">
        {block.bullets.map((bullet) => (
          <li key={bullet}>{bullet}</li>
        ))}
      </ul>
    </article>
  );
}

function DetailListCard({ title, items }) {
  return (
    <article className="detail-card">
      <h3>{title}</h3>
      <ul className="detail-card__list">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </article>
  );
}

export default function OptimizerShowcase({ onClose }) {
  return (
    <div className="showcase-shell">
      <div className="showcase-frame">
        <header className="showcase-header">
          <div className="showcase-header__row">
            <div className="showcase-header__copy">
              <div className="showcase-mark">
                <Plane size={15} />
                <span>Dynamic CI Assistant</span>
              </div>
              <p className="showcase-kicker">System Architecture and Information Flow</p>
              <h1>From simulator state and operational triggers to a pilot-facing speed recommendation.</h1>
              <p className="showcase-lead">
                This view groups the optimizer into runtime stages rather than file-level components. It shows where
                information enters the system, how it is interpreted, where optimization occurs and what is returned
                to the cockpit interface.
              </p>
            </div>

            <aside className="showcase-summary">
              <div className="showcase-summary__label">Deployment Summary</div>
              <div className="showcase-summary__grid">
                <div>
                  <span>Runtime</span>
                  <strong>Windows sim PC</strong>
                </div>
                <div>
                  <span>Interfaces</span>
                  <strong>SimConnect, SimBrief, FastAPI</strong>
                </div>
                <div>
                  <span>Clients</span>
                  <strong>Browser-based EFB</strong>
                </div>
              </div>
              {onClose && (
                <button className="showcase-back-button" onClick={onClose}>
                  Return to EFB
                </button>
              )}
            </aside>
          </div>
        </header>

        <section className="architecture-board">
          <div className="architecture-board__header">
            <div>
              <p className="showcase-kicker">Primary Runtime Path</p>
              <h2>Grouped system view</h2>
            </div>
            <div className="architecture-board__legend">
              <span className="legend-key">
                <span className="legend-key__line" />
                Main flow
              </span>
              <span className="legend-key">
                <span className="legend-key__line legend-key__line--dashed" />
                Optional branch
              </span>
            </div>
          </div>

          <div className="architecture-grid">
            {STAGES.map((stage) => (
              <section className={`architecture-stage architecture-stage--${stage.tone}`.trim()} key={stage.id}>
                <div className="architecture-stage__id">{stage.id}</div>
                <h2>{stage.title}</h2>
                <p className="architecture-stage__summary">{stage.summary}</p>

                <div className="architecture-stage__stack">
                  {stage.blocks.map((block) => (
                    <StageBlock key={block.title} block={block} tone={stage.tone} />
                  ))}
                </div>
              </section>
            ))}
          </div>

          <div className="architecture-board__note">
            Packages are grouped by runtime responsibility so the chart reads like a published system diagram rather
            than a file inventory.
          </div>
        </section>

        <section className="flow-strip">
          {FLOW_STEPS.map((step, index) => (
            <div className="flow-strip__step" key={step}>
              <span className="flow-strip__index">{String(index + 1).padStart(2, "0")}</span>
              <p>{step}</p>
            </div>
          ))}
        </section>

        <section className="detail-grid">
          <DetailListCard title="Cross-links" items={CROSS_LINKS} />
          <DetailListCard title="Published Outputs" items={OUTPUTS} />
          <DetailListCard title="Deployment Notes" items={DEPLOYMENT_NOTES} />
        </section>
      </div>
    </div>
  );
}
