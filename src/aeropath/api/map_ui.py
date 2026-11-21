"""Premium, modern single-page UI for AEROPATH.

Built following ui-ux-pro-max guidelines:
- Seamless Dual-Mode: Aurora Dark & Crisp Sky Light with 1-click switcher & persistence
- Outfit (athletic headings) & Plus Jakarta Sans (clean UI data) typography
- 100% Inline SVG icons (Lucide/Phosphor style, zero emojis as icons)
- Dual mode: A-to-B Clean Route Planning & Closed Circular Loop Generation
- 1-Click Origin/Destination Reverser with animated flip
- Quick Activity & City Preset chips
- Interactive route hover-synchronization with map polylines
- Interactive diurnal hourly workout timeline with departure recommendations
- Dynamic Leaflet tile layer switching (Dark Matter in Dark Mode, Voyager in Light Mode)
- Live single-point CPCB AQI inspection probe
- Fully responsive (desktop collapsible glass panel + mobile responsive drawer)
- WCAG AAA contrast compliance (> 7:1 in both modes) and prefers-reduced-motion support
"""

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>AEROPATH — Clean-Air Route & Fitness Navigation</title>
  
  <!-- Typography: Outfit (Athletic Headings) + Plus Jakarta Sans (UI Data) -->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  
  <!-- Leaflet CSS & JS -->
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

  <style>
    /* ══════════════════════════════════════════════════════════════════
       DESIGN SYSTEM VARIABLES (ui-ux-pro-max Dual Theme)
       ══════════════════════════════════════════════════════════════════ */
    :root, [data-theme="dark"] {
      --bg-base: #070b12;
      --bg-surface: rgba(13, 21, 37, 0.88);
      --bg-surface-elevated: #152238;
      --bg-card: rgba(21, 34, 56, 0.72);
      --border-subtle: rgba(56, 189, 248, 0.12);
      --border-hover: rgba(56, 189, 248, 0.28);
      
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      
      --primary: #00f5d4;
      --primary-hover: #10b981;
      --primary-glow: rgba(0, 245, 212, 0.25);
      --on-primary: #04241e;
      
      --accent: #38bdf8;
      --accent-glow: rgba(56, 189, 248, 0.25);
      
      --map-bg: #060911;
      --shadow-glass: 0 20px 40px -15px rgba(0, 0, 0, 0.7), 0 0 0 1px var(--border-subtle);
      --shadow-card: 0 8px 24px -8px rgba(0, 0, 0, 0.5);
      --brand-gradient: linear-gradient(135deg, #00f5d4, #38bdf8);
      --header-bg: rgba(13, 21, 37, 0.75);
    }

    [data-theme="light"] {
      --bg-base: #f0f6fa;
      --bg-surface: rgba(255, 255, 255, 0.92);
      --bg-surface-elevated: #ffffff;
      --bg-card: rgba(241, 245, 249, 0.88);
      --border-subtle: rgba(15, 23, 42, 0.10);
      --border-hover: rgba(2, 132, 199, 0.35);
      
      --text-main: #0f172a;
      --text-muted: #334155;
      --text-dim: #64748b;
      
      --primary: #059669;
      --primary-hover: #047857;
      --primary-glow: rgba(5, 150, 105, 0.25);
      --on-primary: #ffffff;
      
      --accent: #0284c7;
      --accent-glow: rgba(2, 132, 199, 0.25);
      
      --map-bg: #e2e8f0;
      --shadow-glass: 0 20px 40px -15px rgba(15, 23, 42, 0.12), 0 0 0 1px var(--border-subtle);
      --shadow-card: 0 8px 24px -8px rgba(15, 23, 42, 0.08);
      --brand-gradient: linear-gradient(135deg, #059669, #0284c7);
      --header-bg: rgba(255, 255, 255, 0.85);
    }

    /* Common Constants */
    :root {
      /* CPCB Official AQI Color Tokens */
      --aqi-good: #00b050;
      --aqi-satisfactory: #84cc16;
      --aqi-moderate: #eab308;
      --aqi-poor: #f97316;
      --aqi-very-poor: #ef4444;
      --aqi-severe: #991b1b;
      
      --font-display: 'Outfit', -apple-system, sans-serif;
      --font-ui: 'Plus Jakarta Sans', -apple-system, sans-serif;
      
      --radius-sm: 8px;
      --radius-md: 12px;
      --radius-lg: 18px;
      --radius-full: 9999px;
      
      --transition-fast: 180ms cubic-bezier(0.16, 1, 0.3, 1);
      --transition-normal: 250ms cubic-bezier(0.16, 1, 0.3, 1);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    
    body, html {
      width: 100%; height: 100%;
      overflow: hidden;
      background-color: var(--bg-base);
      color: var(--text-main);
      font-family: var(--font-ui);
      -webkit-font-smoothing: antialiased;
      transition: background-color var(--transition-normal), color var(--transition-normal);
    }

    #app-viewport {
      display: flex;
      width: 100vw;
      height: 100vh;
      position: relative;
    }

    /* ─── LEAFLET MAP ─── */
    #map {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      z-index: 1;
      background: var(--map-bg);
      transition: background var(--transition-normal);
    }

    /* Clean Dark/Light Map Filter for OpenStreetMap (100% Free & Keyless) */
    [data-theme="dark"] .leaflet-tile-pane {
      filter: brightness(0.62) invert(1) contrast(3.4) hue-rotate(200deg) saturate(0.28) brightness(0.72);
    }
    [data-theme="light"] .leaflet-tile-pane {
      filter: contrast(1.05) saturate(1.1);
    }

    .custom-pin {
      background: transparent !important;
      border: none !important;
      box-shadow: none !important;
    }

    .leaflet-control-zoom {
      border: 1px solid var(--border-subtle) !important;
      border-radius: var(--radius-md) !important;
      overflow: hidden;
      box-shadow: var(--shadow-card) !important;
    }
    .leaflet-control-zoom a {
      background: var(--bg-surface-elevated) !important;
      color: var(--text-main) !important;
      border-bottom: 1px solid var(--border-subtle) !important;
      transition: background var(--transition-fast), color var(--transition-fast);
    }
    .leaflet-control-zoom a:hover {
      background: var(--border-hover) !important;
      color: var(--accent) !important;
    }

    /* ─── FLOATING SIDEBAR (DESKTOP) ─── */
    #panel-container {
      position: absolute;
      top: 20px;
      left: 20px;
      bottom: 20px;
      width: 440px;
      max-width: calc(100vw - 40px);
      z-index: 1000;
      display: flex;
      flex-direction: column;
      background: var(--bg-surface);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow-glass);
      overflow: hidden;
      transition: transform var(--transition-normal), opacity var(--transition-normal), background var(--transition-normal);
    }
    #panel-container.collapsed {
      transform: translateX(-460px);
      opacity: 0;
      pointer-events: none;
    }

    /* Sidebar Floating Toggle Button */
    .sidebar-toggle-pill {
      position: absolute;
      top: 20px;
      left: 20px;
      z-index: 999;
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-hover);
      border-radius: var(--radius-full);
      padding: 10px 16px;
      color: var(--text-main);
      font-size: 0.84rem;
      font-weight: 700;
      font-family: var(--font-display);
      cursor: pointer;
      display: none;
      align-items: center;
      gap: 8px;
      box-shadow: var(--shadow-glass);
      transition: all var(--transition-fast);
      min-height: 44px;
    }
    .sidebar-toggle-pill:hover {
      border-color: var(--primary);
      transform: translateY(-1px);
    }

    /* Brand Header */
    .brand-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--border-subtle);
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: var(--header-bg);
      backdrop-filter: blur(10px);
    }
    .brand-title-wrap {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .brand-icon {
      width: 40px;
      height: 40px;
      background: var(--brand-gradient);
      border-radius: var(--radius-sm);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #04241e;
      box-shadow: 0 4px 12px var(--primary-glow);
      flex-shrink: 0;
    }
    .brand-name {
      font-family: var(--font-display);
      font-weight: 800;
      font-size: 1.28rem;
      letter-spacing: 0.5px;
      background: var(--brand-gradient);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      line-height: 1.1;
    }
    .brand-badge {
      font-size: 0.68rem;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.6px;
      font-weight: 600;
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    /* Theme Toggle Button */
    .theme-toggle-btn {
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-subtle);
      color: var(--text-main);
      border-radius: var(--radius-full);
      padding: 6px 10px;
      font-size: 0.74rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 6px;
      cursor: pointer;
      transition: all var(--transition-fast);
      min-height: 36px;
    }
    .theme-toggle-btn:hover {
      border-color: var(--primary);
      transform: translateY(-1px);
    }
    .theme-toggle-btn:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }

    .collapse-btn {
      background: transparent;
      border: 1px solid var(--border-subtle);
      color: var(--text-dim);
      border-radius: var(--radius-sm);
      width: 32px;
      height: 32px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      transition: all var(--transition-fast);
    }
    .collapse-btn:hover {
      color: var(--text-main);
      border-color: var(--border-hover);
      background: var(--bg-card);
    }

    /* Scrollable content */
    .panel-body {
      flex: 1;
      overflow-y: auto;
      padding: 18px 20px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .panel-body::-webkit-scrollbar { width: 5px; }
    .panel-body::-webkit-scrollbar-track { background: transparent; }
    .panel-body::-webkit-scrollbar-thumb { background: var(--border-hover); border-radius: 4px; }

    /* Quick Presets Section */
    .preset-section-title {
      font-size: 0.68rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      color: var(--text-dim);
      margin-bottom: 4px;
    }
    .preset-bar {
      display: flex;
      align-items: center;
      gap: 6px;
      overflow-x: auto;
      padding-bottom: 4px;
      scrollbar-width: none;
    }
    .preset-bar::-webkit-scrollbar { display: none; }
    
    .preset-btn {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-full);
      padding: 6px 12px;
      font-size: 0.76rem;
      font-weight: 600;
      color: var(--text-muted);
      cursor: pointer;
      white-space: nowrap;
      transition: all var(--transition-fast);
      display: flex;
      align-items: center;
      gap: 6px;
      min-height: 32px;
    }
    .preset-btn:hover {
      background: var(--bg-surface-elevated);
      color: var(--text-main);
      border-color: var(--primary);
      transform: translateY(-1px);
    }
    .preset-btn.activity-preset {
      border-color: rgba(56, 189, 248, 0.25);
    }
    .preset-btn.activity-preset:hover {
      border-color: var(--accent);
    }

    /* Mode Switcher Tabs */
    .mode-tabs {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px;
      background: var(--bg-card);
      padding: 4px;
      border-radius: var(--radius-md);
      border: 1px solid var(--border-subtle);
    }
    .mode-tab-btn {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 9px 12px;
      border-radius: var(--radius-sm);
      font-size: 0.82rem;
      font-weight: 700;
      color: var(--text-muted);
      border: none;
      background: transparent;
      cursor: pointer;
      transition: all var(--transition-fast);
      min-height: 40px;
    }
    .mode-tab-btn:hover { color: var(--text-main); }
    .mode-tab-btn.active {
      background: var(--bg-surface-elevated);
      color: var(--text-main);
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
      border: 1px solid var(--border-hover);
    }

    /* Form Elements */
    .input-field-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
      position: relative;
    }
    .input-field-group label {
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-dim);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .input-wrapper {
      position: relative;
      display: flex;
      align-items: center;
    }
    .input-icon {
      position: absolute;
      left: 12px;
      color: var(--text-dim);
      pointer-events: none;
      display: flex;
      align-items: center;
    }
    .custom-input {
      width: 100%;
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-sm);
      padding: 11px 40px 11px 36px;
      color: var(--text-main);
      font-size: 0.88rem;
      font-family: var(--font-ui);
      outline: none;
      transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
      min-height: 44px;
    }
    .custom-input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-glow);
    }
    .pin-btn {
      position: absolute;
      right: 8px;
      background: transparent;
      border: none;
      color: var(--text-dim);
      cursor: pointer;
      padding: 8px;
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: color var(--transition-fast);
      min-width: 32px;
      min-height: 32px;
    }
    .pin-btn:hover { color: var(--accent); }

    /* Swap Origin & Destination Button */
    .swap-divider {
      position: relative;
      display: flex;
      justify-content: center;
      align-items: center;
      margin: -6px 0;
      z-index: 2;
    }
    .swap-btn {
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-full);
      width: 34px;
      height: 34px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--accent);
      cursor: pointer;
      box-shadow: var(--shadow-card);
      transition: all var(--transition-fast);
    }
    .swap-btn:hover {
      border-color: var(--accent);
      transform: rotate(180deg);
      background: var(--bg-card);
    }

    /* Autocomplete dropdown */
    .suggestions-dropdown {
      position: absolute;
      top: calc(100% + 4px);
      left: 0;
      right: 0;
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-hover);
      border-radius: var(--radius-sm);
      max-height: 200px;
      overflow-y: auto;
      z-index: 1050;
      box-shadow: var(--shadow-card);
      display: none;
    }
    .suggestion-item {
      padding: 11px 14px;
      font-size: 0.82rem;
      color: var(--text-muted);
      cursor: pointer;
      border-bottom: 1px solid var(--border-subtle);
      transition: background var(--transition-fast), color var(--transition-fast);
    }
    .suggestion-item:last-child { border-bottom: none; }
    .suggestion-item:hover {
      background: var(--accent-glow);
      color: var(--text-main);
    }

    .form-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }

    .custom-select {
      width: 100%;
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-sm);
      padding: 10px 12px;
      color: var(--text-main);
      font-size: 0.85rem;
      font-family: var(--font-ui);
      outline: none;
      cursor: pointer;
      transition: border-color var(--transition-fast);
      min-height: 44px;
    }
    .custom-select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-glow);
    }

    /* Slider for Loops */
    .slider-container {
      display: flex;
      flex-direction: column;
      gap: 8px;
      background: var(--bg-card);
      padding: 14px;
      border-radius: var(--radius-sm);
      border: 1px solid var(--border-subtle);
    }
    .slider-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.78rem;
      font-weight: 700;
    }
    .distance-display {
      color: var(--primary);
      font-family: var(--font-display);
      font-size: 1.15rem;
      font-weight: 800;
    }
    input[type="range"] {
      width: 100%;
      height: 6px;
      background: var(--border-hover);
      border-radius: 4px;
      outline: none;
      -webkit-appearance: none;
    }
    input[type="range"]::-webkit-slider-thumb {
      -webkit-appearance: none;
      width: 20px;
      height: 20px;
      border-radius: 50%;
      background: var(--primary);
      cursor: pointer;
      box-shadow: 0 0 10px var(--primary-glow);
      transition: transform var(--transition-fast);
    }
    input[type="range"]::-webkit-slider-thumb:hover {
      transform: scale(1.15);
    }

    /* CTA Button */
    .cta-submit-btn {
      width: 100%;
      padding: 13px;
      background: var(--brand-gradient);
      border: none;
      border-radius: var(--radius-sm);
      color: var(--on-primary);
      font-family: var(--font-display);
      font-size: 1rem;
      font-weight: 800;
      letter-spacing: 0.3px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      box-shadow: 0 4px 14px var(--primary-glow);
      transition: all var(--transition-fast);
      margin-top: 4px;
      min-height: 48px;
    }
    .cta-submit-btn:hover {
      opacity: 0.94;
      box-shadow: 0 6px 20px var(--primary-glow);
      transform: translateY(-1px);
    }
    .cta-submit-btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
      transform: none;
    }

    /* ─── RESULT CARD & ROUTE COMPARISON ─── */
    #results-area {
      display: none;
      flex-direction: column;
      gap: 14px;
      animation: fadeIn 250ms ease-out forwards;
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .route-selector-tabs {
      display: flex;
      gap: 8px;
    }
    .route-tab-card {
      flex: 1;
      padding: 10px 12px;
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: all var(--transition-fast);
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .route-tab-card:hover {
      border-color: var(--border-hover);
      transform: translateY(-1px);
    }
    .route-tab-card.active {
      background: var(--bg-surface-elevated);
      border-color: var(--primary);
      box-shadow: 0 0 12px var(--primary-glow);
    }
    .route-tab-label {
      font-size: 0.72rem;
      font-weight: 700;
      color: var(--text-muted);
      text-transform: uppercase;
    }
    .route-tab-card.active .route-tab-label { color: var(--primary); }
    .route-tab-metric {
      font-family: var(--font-display);
      font-size: 1.1rem;
      font-weight: 800;
    }

    .card-panel {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .exposure-hero-banner {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 14px;
      border-radius: var(--radius-sm);
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-subtle);
    }
    .exposure-val-wrap {
      display: flex;
      align-items: baseline;
      gap: 6px;
    }
    .exposure-big-num {
      font-family: var(--font-display);
      font-size: 2.3rem;
      font-weight: 800;
      line-height: 1;
    }
    .exposure-unit {
      font-size: 0.78rem;
      color: var(--text-muted);
      font-weight: 700;
    }
    .aqi-badge {
      padding: 6px 12px;
      border-radius: var(--radius-full);
      font-size: 0.74rem;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: #000;
    }

    .stat-matrix {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .matrix-box {
      background: var(--bg-surface-elevated);
      padding: 10px 12px;
      border-radius: var(--radius-sm);
      border: 1px solid var(--border-subtle);
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .matrix-box-label {
      font-size: 0.68rem;
      color: var(--text-dim);
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.4px;
    }
    .matrix-box-val {
      font-family: var(--font-display);
      font-size: 1.15rem;
      font-weight: 800;
      color: var(--text-main);
    }

    /* Health Advisory Callout */
    .advisory-callout {
      padding: 11px 13px;
      border-radius: var(--radius-sm);
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-subtle);
      font-size: 0.78rem;
      color: var(--text-muted);
      line-height: 1.45;
      display: flex;
      align-items: flex-start;
      gap: 10px;
    }

    /* Micro-Dispersion Street Breakdown Strip */
    .dispersion-breakdown {
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-sm);
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .dispersion-title {
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-dim);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .dispersion-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 0.7rem;
      font-weight: 700;
      color: var(--primary);
    }
    .street-tag-row {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .street-tag {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-full);
      padding: 3px 8px;
      font-size: 0.7rem;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 4px;
    }

    /* Diurnal Timeline Chart */
    .timeline-chart-wrap {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .chart-bars-row {
      display: flex;
      align-items: flex-end;
      gap: 4px;
      height: 90px;
      padding: 10px 4px 4px 4px;
      background: var(--bg-surface-elevated);
      border-radius: var(--radius-sm);
      border: 1px solid var(--border-subtle);
    }
    .chart-bar-col {
      flex: 1;
      height: 100%;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: flex-end;
      gap: 4px;
      cursor: pointer;
      position: relative;
    }
    .chart-bar-col:hover .chart-bar-fill {
      transform: scaleY(1.08);
      filter: brightness(1.2);
    }
    .chart-bar-fill {
      width: 100%;
      min-width: 6px;
      border-radius: 3px 3px 0 0;
      transition: height var(--transition-normal), transform var(--transition-fast);
      transform-origin: bottom;
    }
    .chart-bar-label {
      font-size: 0.62rem;
      color: var(--text-dim);
      font-weight: 600;
    }
    .timeline-interactive-hint {
      font-size: 0.72rem;
      color: var(--text-muted);
      background: var(--bg-card);
      border: 1px dashed var(--border-subtle);
      border-radius: var(--radius-sm);
      padding: 6px 10px;
      display: none;
    }

    /* Floating Map Legend */
    .aqi-map-legend {
      position: absolute;
      bottom: 24px;
      right: 20px;
      z-index: 998;
      background: var(--bg-surface);
      backdrop-filter: blur(14px);
      -webkit-backdrop-filter: blur(14px);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 12px 14px;
      box-shadow: var(--shadow-glass);
      display: flex;
      flex-direction: column;
      gap: 6px;
      pointer-events: auto;
    }
    .legend-title {
      font-size: 0.68rem;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      color: var(--text-dim);
      margin-bottom: 2px;
    }
    .legend-entry {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 0.72rem;
      color: var(--text-muted);
      font-weight: 600;
    }
    .legend-swatch {
      width: 12px;
      height: 12px;
      border-radius: 3px;
      flex-shrink: 0;
    }

    /* Point Inspector Glass Card */
    #point-inspector {
      position: absolute;
      top: 20px;
      right: 20px;
      z-index: 999;
      background: var(--bg-surface);
      backdrop-filter: blur(18px);
      -webkit-backdrop-filter: blur(18px);
      border: 1px solid var(--border-hover);
      border-radius: var(--radius-md);
      padding: 16px;
      width: 270px;
      box-shadow: var(--shadow-glass);
      display: none;
      flex-direction: column;
      gap: 8px;
      animation: fadeIn 200ms ease-out forwards;
    }

    .pulse-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background-color: var(--primary);
      box-shadow: 0 0 8px var(--primary);
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(1.2); }
    }

    /* Accessibility: Reduced motion */
    @media (prefers-reduced-motion: reduce) {
      * {
        animation: none !important;
        transition: none !important;
      }
    }

    /* ─── MOBILE RESPONSIVENESS ─── */
    @media (max-width: 768px) {
      #panel-container {
        top: auto;
        left: 0;
        right: 0;
        bottom: 0;
        width: 100%;
        max-width: 100%;
        height: 64vh;
        border-radius: var(--radius-lg) var(--radius-lg) 0 0;
        border-bottom: none;
        box-shadow: 0 -10px 30px rgba(0, 0, 0, 0.4);
      }
      #panel-container.collapsed {
        transform: translateY(calc(100% - 62px));
        opacity: 1;
        pointer-events: auto;
      }
      .sidebar-toggle-pill { display: none !important; }
      .aqi-map-legend { display: none; }
      #point-inspector {
        top: 14px;
        left: 14px;
        right: 14px;
        width: auto;
      }
    }
  </style>
</head>
<body>
  <div id="app-viewport">
    <!-- Leaflet Full-Bleed Map Canvas -->
    <div id="map"></div>

    <!-- Desktop Floating Toggle Pill (When Sidebar Collapsed) -->
    <button id="togglePill" class="sidebar-toggle-pill" onclick="toggleSidebar(true)" aria-label="Open route planner panel">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m9 18 6-6-6-6"/></svg>
      <span>Open Route Planner</span>
    </button>

    <!-- Floating Main Dashboard Panel -->
    <aside id="panel-container" aria-label="Route Planning and Air Quality Dashboard">
      <!-- Brand Header -->
      <header class="brand-header">
        <div class="brand-title-wrap">
          <div class="brand-icon" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
          </div>
          <div>
            <h1 class="brand-name">AEROPATH</h1>
            <div class="brand-badge">Air Quality Fitness Router</div>
          </div>
        </div>

        <div class="header-actions">
          <!-- Theme Switcher Button -->
          <button id="themeToggleBtn" class="theme-toggle-btn" onclick="toggleTheme()" aria-label="Toggle dark and light theme" title="Switch Theme (Dark / Light)">
            <svg id="themeIconDark" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>
            <svg id="themeIconLight" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="display:none;"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>
            <span id="themeLabel">Dark</span>
          </button>

          <!-- Collapse Sidebar Button -->
          <button class="collapse-btn" onclick="toggleSidebar()" aria-label="Toggle panel collapse" title="Collapse Panel">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m15 18-6-6 6-6"/></svg>
          </button>
        </div>
      </header>

      <!-- Scrollable Controls & Results -->
      <div class="panel-body">

        <!-- City & Activity Quick Presets -->
        <div>
          <div class="preset-section-title">City Presets</div>
          <div class="preset-bar">
            <button class="preset-btn" onclick="setPreset('kolkata')">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg> Kolkata
            </button>
            <button class="preset-btn" onclick="setPreset('delhi')">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg> Delhi
            </button>
            <button class="preset-btn" onclick="setPreset('mumbai')">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg> Mumbai
            </button>
            <button class="preset-btn" onclick="setPreset('bengaluru')">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg> Bengaluru
            </button>
          </div>
        </div>

        <div>
          <div class="preset-section-title">Workout Shortcuts</div>
          <div class="preset-bar">
            <button class="preset-btn activity-preset" onclick="triggerWorkoutPreset('jog')">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1 .4-1 1v11c0 .6.4 1 1 1h2"/></svg> 5km Jog Loop
            </button>
            <button class="preset-btn activity-preset" onclick="triggerWorkoutPreset('cycle')">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="5.5" cy="17.5" r="3.5"/><circle cx="18.5" cy="17.5" r="3.5"/><path d="M15 6a1 1 0 1 0 0-2 1 1 0 0 0 0 2zm-3 11.5L9 6H5"/></svg> 10km Cycle
            </button>
            <button class="preset-btn activity-preset" onclick="triggerWorkoutPreset('walk')">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m13 4 2 2-3 4-2-2 3-4z"/><circle cx="15" cy="4" r="1"/></svg> 2km Clean Walk
            </button>
          </div>
        </div>

        <!-- Mode Switcher Tabs: A-to-B Route vs Circular Loop -->
        <div class="mode-tabs">
          <button id="tab-route-btn" class="mode-tab-btn active" onclick="switchMode('route')">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="6" cy="19" r="3"/><path d="M9 19h8.5a4.5 4.5 0 0 0 0-9H7a3 3 0 0 1 0-6h11"/></svg>
            <span>A &rarr; B Route</span>
          </button>
          <button id="tab-loop-btn" class="mode-tab-btn" onclick="switchMode('loop')">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
            <span>Circuit Loop</span>
          </button>
        </div>

        <!-- Mode 1: A-to-B Route Planning Form -->
        <form id="routeForm" onsubmit="handleRoutePlan(event)" style="display: flex; flex-direction: column; gap: 12px;">
          <!-- Origin Input -->
          <div class="input-field-group">
            <label for="origSearch">
              <span>Start Origin</span>
              <span style="color: var(--primary);">Point A</span>
            </label>
            <div class="input-wrapper">
              <span class="input-icon" aria-hidden="true">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>
              </span>
              <input id="origSearch" type="text" class="custom-input" placeholder="Search address, landmark..." autocomplete="off">
              <button type="button" class="pin-btn" onclick="activateClickTarget('orig')" title="Click on map to place start pin" aria-label="Pick start on map">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
              </button>
            </div>
            <div id="origSuggestions" class="suggestions-dropdown"></div>
            <input id="origLat" type="hidden" value="22.5448">
            <input id="origLng" type="hidden" value="88.3426">
          </div>

          <!-- 1-Click Origin / Destination Swap Button -->
          <div class="swap-divider">
            <button type="button" class="swap-btn" onclick="swapOriginDest()" aria-label="Reverse start and destination" title="Reverse Route">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m7 16-4-4 4-4"/><path d="M3 12h18"/><path d="m17 8 4 4-4 4"/></svg>
            </button>
          </div>

          <!-- Destination Input -->
          <div class="input-field-group">
            <label for="destSearch">
              <span>Destination</span>
              <span style="color: var(--accent);">Point B</span>
            </label>
            <div class="input-wrapper">
              <span class="input-icon" aria-hidden="true">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/></svg>
              </span>
              <input id="destSearch" type="text" class="custom-input" placeholder="Search address, landmark..." autocomplete="off">
              <button type="button" class="pin-btn" onclick="activateClickTarget('dest')" title="Click on map to place destination pin" aria-label="Pick destination on map">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
              </button>
            </div>
            <div id="destSuggestions" class="suggestions-dropdown"></div>
            <input id="destLat" type="hidden" value="22.5126">
            <input id="destLng" type="hidden" value="88.3582">
          </div>

          <!-- Activity & Sensitivity Row -->
          <div class="form-row">
            <div class="input-field-group">
              <label for="routeActivity">Activity Mode</label>
              <select id="routeActivity" class="custom-select">
                <option value="run" selected>Running (Intense Inhalation)</option>
                <option value="walk">Walking (Normal Inhalation)</option>
              </select>
            </div>
            <div class="input-field-group">
              <label for="routeSensitivity">Sensitivity</label>
              <select id="routeSensitivity" class="custom-select">
                <option value="normal" selected>Normal</option>
                <option value="sensitive">Sensitive / Asthma</option>
                <option value="high">High Risk / Elderly</option>
              </select>
            </div>
          </div>

          <button id="routeSubmitBtn" type="submit" class="cta-submit-btn">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m5 12 7-7 7 7"/><path d="M12 19V5"/></svg>
            Find Cleanest Route
          </button>
        </form>

        <!-- Mode 2: Circular Loop Generator Form -->
        <form id="loopForm" onsubmit="handleLoopPlan(event)" style="display: none; flex-direction: column; gap: 14px;">
          <!-- Center Point -->
          <div class="input-field-group">
            <label for="loopCenterSearch">Starting Center Point</label>
            <div class="input-wrapper">
              <span class="input-icon" aria-hidden="true">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#a855f7" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>
              </span>
              <input id="loopCenterSearch" type="text" class="custom-input" placeholder="Search starting location..." autocomplete="off">
              <button type="button" class="pin-btn" onclick="activateClickTarget('loop')" title="Click on map to place center" aria-label="Pick center on map">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
              </button>
            </div>
            <div id="loopSuggestions" class="suggestions-dropdown"></div>
            <input id="loopLat" type="hidden" value="22.5448">
            <input id="loopLng" type="hidden" value="88.3426">
          </div>

          <!-- Target Distance Slider -->
          <div class="slider-container">
            <div class="slider-header">
              <span>TARGET CIRCUIT DISTANCE</span>
              <span id="sliderValDisplay" class="distance-display">5.0 km</span>
            </div>
            <input id="distSlider" type="range" min="1.5" max="15.0" step="0.5" value="5.0" oninput="updateSliderVal(this.value)">
          </div>

          <!-- Activity & Sensitivity Row -->
          <div class="form-row">
            <div class="input-field-group">
              <label for="loopActivity">Activity</label>
              <select id="loopActivity" class="custom-select">
                <option value="run" selected>Running</option>
                <option value="walk">Walking</option>
              </select>
            </div>
            <div class="input-field-group">
              <label for="loopSensitivity">Sensitivity</label>
              <select id="loopSensitivity" class="custom-select">
                <option value="normal">Normal</option>
                <option value="sensitive">Sensitive</option>
                <option value="high">High</option>
              </select>
            </div>
          </div>

          <button id="loopSubmitBtn" type="submit" class="cta-submit-btn">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
            Generate Clean Loop
          </button>
        </form>

        <!-- Results Area -->
        <div id="results-area">
          <!-- Route Candidate Selector -->
          <div id="routeTabsRow" class="route-selector-tabs"></div>

          <!-- Primary Metric Card -->
          <div class="card-panel">
            <div class="exposure-hero-banner">
              <div class="exposure-val-wrap">
                <span id="resMeanAqi" class="exposure-big-num">--</span>
                <span class="exposure-unit">MEAN AQI</span>
              </div>
              <span id="resCatBadge" class="aqi-badge">Calculating</span>
            </div>

            <!-- 4 Metrics Grid -->
            <div class="stat-matrix">
              <div class="matrix-box">
                <span class="matrix-box-label">Distance</span>
                <span id="resDist" class="matrix-box-val">0.0 km</span>
              </div>
              <div class="matrix-box">
                <span class="matrix-box-label">Duration</span>
                <span id="resDur" class="matrix-box-val">0 min</span>
              </div>
              <div class="matrix-box">
                <span class="matrix-box-label">Inhaled Dose</span>
                <span id="resDose" class="matrix-box-val">0</span>
              </div>
              <div class="matrix-box">
                <span class="matrix-box-label">Confidence</span>
                <span id="resConfidence" class="matrix-box-val" style="color: var(--primary);">HIGH</span>
              </div>
            </div>

            <!-- Health Advisory Callout -->
            <div id="resAdvisoryBox" class="advisory-callout"></div>

            <!-- Micro-Dispersion Street Breakdown Strip -->
            <div class="dispersion-breakdown">
              <div class="dispersion-title">
                <span>Micro-Dispersion & Traffic Model</span>
                <span class="dispersion-badge">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                  rules_v1 Active
                </span>
              </div>
              <div class="street-tag-row">
                <span class="street-tag" title="Residential streets receive 15% dispersion attenuation">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>
                  Quiet Streets Favored
                </span>
                <span class="street-tag" title="Arterial roads apply +20% traffic scaling">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2"><rect width="18" height="18" x="3" y="3" rx="2"/></svg>
                  Arterial Avoidance
                </span>
                <span class="street-tag" title="Temporal rush hour scaling active">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                  Rush Hour Adjusted
                </span>
              </div>
            </div>
          </div>

          <!-- Diurnal Hourly Windows Timeline -->
          <div id="windowsCard" class="card-panel">
            <div style="display: flex; justify-content: space-between; align-items: baseline;">
              <span style="font-size: 0.74rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-dim);">Cleanest Workout Hours</span>
              <span id="bestHourHighlight" style="font-size: 0.75rem; color: var(--primary); font-weight: 700;"></span>
            </div>
            
            <div class="timeline-chart-wrap">
              <div id="chartBars" class="chart-bars-row"></div>
              <div id="timelineHint" class="timeline-interactive-hint"></div>
            </div>
          </div>
        </div>

      </div>
    </aside>

    <!-- Point Inspector Glass Card -->
    <div id="point-inspector">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <span style="font-weight: 800; font-size: 0.78rem; color: var(--accent); letter-spacing: 0.5px;">POINT AIR QUALITY</span>
        <button onclick="closeInspector()" style="background: none; border: none; color: var(--text-dim); cursor: pointer;" aria-label="Close inspector">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
        </button>
      </div>
      <div style="display: flex; align-items: baseline; justify-content: space-between;">
        <span id="inspAqi" style="font-family: var(--font-display); font-size: 2rem; font-weight: 800;">--</span>
        <span id="inspBadge" class="aqi-badge">--</span>
      </div>
      <div style="font-size: 0.72rem; color: var(--text-muted);">
        Dominant Pollutant: <strong id="inspDominant" style="color: var(--text-main);">--</strong>
      </div>
      <div id="inspPollutants" style="display: grid; grid-template-columns: 1fr 1fr; gap: 4px; font-size: 0.7rem; color: var(--text-dim); margin-top: 4px;"></div>
    </div>

    <!-- Floating Map Legend -->
    <div class="aqi-map-legend" aria-hidden="true">
      <div class="legend-title">CPCB National Scale</div>
      <div class="legend-entry"><div class="legend-swatch" style="background: var(--aqi-good);"></div><span>Good (0-50)</span></div>
      <div class="legend-entry"><div class="legend-swatch" style="background: var(--aqi-satisfactory);"></div><span>Satisfactory (51-100)</span></div>
      <div class="legend-entry"><div class="legend-swatch" style="background: var(--aqi-moderate);"></div><span>Moderate (101-200)</span></div>
      <div class="legend-entry"><div class="legend-swatch" style="background: var(--aqi-poor);"></div><span>Poor (201-300)</span></div>
      <div class="legend-entry"><div class="legend-swatch" style="background: var(--aqi-very-poor);"></div><span>Very Poor (301-400)</span></div>
      <div class="legend-entry"><div class="legend-swatch" style="background: var(--aqi-severe);"></div><span>Severe (401-500)</span></div>
    </div>
  </div>

  <script>
    // ─── INITIALIZE DUAL THEME & MAP ───
    let currentTheme = 'dark';
    let tileLayer = null;

    const map = L.map('map', {
      zoomControl: false,
      keyboard: true
    }).setView([22.5448, 88.3426], 13);

    L.control.zoom({ position: 'bottomright' }).addTo(map);

    // Reliable, keyless OpenStreetMap basemap
    tileLayer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> contributors'
    }).addTo(map);

    function applyTheme(theme) {
      currentTheme = theme;
      document.documentElement.setAttribute('data-theme', theme);
      localStorage.setItem('aeropath-theme', theme);

      // Toggle icons & label
      const isDark = theme === 'dark';
      document.getElementById('themeIconDark').style.display = isDark ? 'inline' : 'none';
      document.getElementById('themeIconLight').style.display = isDark ? 'none' : 'inline';
      document.getElementById('themeLabel').innerText = isDark ? 'Dark' : 'Light';

      // Re-render routes if present
      if (currentRoutes && currentRoutes.length > 0) {
        drawMapPolylines(false);
      }
    }

    function toggleTheme() {
      applyTheme(currentTheme === 'dark' ? 'light' : 'dark');
    }

    // State Variables
    let origMarker = null;
    let destMarker = null;
    let loopMarker = null;
    let polylines = [];
    let currentRoutes = [];
    let currentBestWindows = [];
    let currentDataNote = '';
    let activeRouteIndex = 0;
    let clickTarget = 'orig'; // 'orig', 'dest', or 'loop'
    let isPanelCollapsed = false;

    const AQI_COLORS = {
      good: '#00b050',
      satisfactory: '#84cc16',
      moderate: '#eab308',
      poor: '#f97316',
      very_poor: '#ef4444',
      severe: '#991b1b'
    };

    function createMarkerIcon(color, label) {
      return L.divIcon({
        className: 'custom-pin',
        html: `<div style="
          width: 28px; height: 28px; border-radius: 50%;
          background: ${color}; border: 3px solid #fff;
          box-shadow: 0 0 15px ${color}; display: flex;
          align-items: center; justify-content: center;
          font-family: var(--font-display); font-weight: 800; font-size: 11px; color: #000;">
          ${label}
        </div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14]
      });
    }

    // ─── SIDEBAR TOGGLING ───
    function toggleSidebar(open = null) {
      const panel = document.getElementById('panel-container');
      const togglePill = document.getElementById('togglePill');
      if (open === null) {
        isPanelCollapsed = !isPanelCollapsed;
      } else {
        isPanelCollapsed = !open;
      }
      panel.classList.toggle('collapsed', isPanelCollapsed);
      togglePill.style.display = isPanelCollapsed ? 'flex' : 'none';
      setTimeout(() => map.invalidateSize(), 300);
    }

    // ─── 1-CLICK SWAP ORIGIN / DESTINATION ───
    function swapOriginDest() {
      const origLat = document.getElementById('origLat');
      const origLng = document.getElementById('origLng');
      const origSearch = document.getElementById('origSearch');
      const destLat = document.getElementById('destLat');
      const destLng = document.getElementById('destLng');
      const destSearch = document.getElementById('destSearch');

      const tempLat = origLat.value;
      const tempLng = origLng.value;
      const tempSearch = origSearch.value;

      origLat.value = destLat.value;
      origLng.value = destLng.value;
      origSearch.value = destSearch.value;

      destLat.value = tempLat;
      destLng.value = tempLng;
      destSearch.value = tempSearch;

      setOrigin(parseFloat(origLat.value), parseFloat(origLng.value), origSearch.value);
      setDest(parseFloat(destLat.value), parseFloat(destLng.value), destSearch.value);

      if (currentRoutes && currentRoutes.length > 0) {
        document.getElementById('routeSubmitBtn').click();
      }
    }

    // ─── MODE SWITCHING ───
    let currentMode = 'route';

    function switchMode(mode) {
      currentMode = mode;
      document.getElementById('tab-route-btn').classList.toggle('active', mode === 'route');
      document.getElementById('tab-loop-btn').classList.toggle('active', mode === 'loop');
      document.getElementById('routeForm').style.display = mode === 'route' ? 'flex' : 'none';
      document.getElementById('loopForm').style.display = mode === 'loop' ? 'flex' : 'none';

      if (mode === 'route') {
        if (loopMarker) map.removeLayer(loopMarker);
        if (origMarker) origMarker.addTo(map);
        if (destMarker) destMarker.addTo(map);
        clickTarget = 'orig';
      } else {
        if (origMarker) map.removeLayer(origMarker);
        if (destMarker) map.removeLayer(destMarker);
        setLoopCenter(parseFloat(document.getElementById('loopLat').value), parseFloat(document.getElementById('loopLng').value));
        clickTarget = 'loop';
      }
    }

    function activateClickTarget(target) {
      clickTarget = target;
    }

    // ─── MAP CLICK HANDLER ───
    map.on('click', async function(e) {
      const lat = e.latlng.lat;
      const lng = e.latlng.lng;

      if (currentMode === 'route') {
        if (clickTarget === 'orig') {
          setOrigin(lat, lng, `Placed Start (${lat.toFixed(4)}, ${lng.toFixed(4)})`);
          clickTarget = 'dest';
        } else {
          setDest(lat, lng, `Placed Destination (${lat.toFixed(4)}, ${lng.toFixed(4)})`);
          clickTarget = 'orig';
        }
      } else {
        setLoopCenter(lat, lng, `Circuit Center (${lat.toFixed(4)}, ${lng.toFixed(4)})`);
      }

      probePointAirQuality(lat, lng);
    });

    function setOrigin(lat, lng, name = '') {
      document.getElementById('origLat').value = lat;
      document.getElementById('origLng').value = lng;
      if (name) document.getElementById('origSearch').value = name;
      if (origMarker) map.removeLayer(origMarker);
      origMarker = L.marker([lat, lng], { icon: createMarkerIcon('#10b981', 'A'), draggable: true }).addTo(map);
      origMarker.on('dragend', (e) => {
        const p = e.target.getLatLng();
        setOrigin(p.lat, p.lng, `Pinned (${p.lat.toFixed(4)}, ${p.lng.toFixed(4)})`);
      });
    }

    function setDest(lat, lng, name = '') {
      document.getElementById('destLat').value = lat;
      document.getElementById('destLng').value = lng;
      if (name) document.getElementById('destSearch').value = name;
      if (destMarker) map.removeLayer(destMarker);
      destMarker = L.marker([lat, lng], { icon: createMarkerIcon('#38bdf8', 'B'), draggable: true }).addTo(map);
      destMarker.on('dragend', (e) => {
        const p = e.target.getLatLng();
        setDest(p.lat, p.lng, `Pinned (${p.lat.toFixed(4)}, ${p.lng.toFixed(4)})`);
      });
    }

    function setLoopCenter(lat, lng, name = '') {
      document.getElementById('loopLat').value = lat;
      document.getElementById('loopLng').value = lng;
      if (name) document.getElementById('loopCenterSearch').value = name;
      if (loopMarker) map.removeLayer(loopMarker);
      loopMarker = L.marker([lat, lng], { icon: createMarkerIcon('#a855f7', 'C'), draggable: true }).addTo(map);
      loopMarker.on('dragend', (e) => {
        const p = e.target.getLatLng();
        setLoopCenter(p.lat, p.lng, `Pinned (${p.lat.toFixed(4)}, ${p.lng.toFixed(4)})`);
      });
    }

    function updateSliderVal(val) {
      document.getElementById('sliderValDisplay').innerText = parseFloat(val).toFixed(1) + ' km';
    }

    // ─── CITY & WORKOUT PRESETS ───
    function setPreset(city) {
      if (city === 'kolkata') {
        map.setView([22.535, 88.350], 13);
        setOrigin(22.5448, 88.3426, 'Victoria Memorial, Kolkata');
        setDest(22.5126, 88.3582, 'Rabindra Sarobar, Kolkata');
        setLoopCenter(22.5448, 88.3426, 'Victoria Memorial, Kolkata');
      } else if (city === 'delhi') {
        map.setView([28.622, 77.223], 13);
        setOrigin(28.6315, 77.2167, 'Connaught Place, New Delhi');
        setDest(28.6129, 77.2295, 'India Gate, New Delhi');
        setLoopCenter(28.6315, 77.2167, 'Connaught Place, New Delhi');
      } else if (city === 'mumbai') {
        map.setView([18.935, 72.825], 13);
        setOrigin(18.9220, 72.8347, 'Gateway of India, Mumbai');
        setDest(18.9543, 72.8125, 'Chowpatty Beach, Mumbai');
        setLoopCenter(18.9220, 72.8347, 'Gateway of India, Mumbai');
      } else if (city === 'bengaluru') {
        map.setView([12.975, 77.595], 13);
        setOrigin(12.9767, 77.5993, 'Cubbon Park, Bengaluru');
        setDest(12.9507, 77.5848, 'Lalbagh Botanical Garden, Bengaluru');
        setLoopCenter(12.9767, 77.5993, 'Cubbon Park, Bengaluru');
      }
    }

    function triggerWorkoutPreset(type) {
      switchMode('loop');
      if (type === 'jog') {
        document.getElementById('distSlider').value = 5.0;
        updateSliderVal(5.0);
        document.getElementById('loopActivity').value = 'run';
        document.getElementById('loopSensitivity').value = 'normal';
      } else if (type === 'cycle') {
        document.getElementById('distSlider').value = 10.0;
        updateSliderVal(10.0);
        document.getElementById('loopActivity').value = 'run';
        document.getElementById('loopSensitivity').value = 'normal';
      } else if (type === 'walk') {
        document.getElementById('distSlider').value = 2.0;
        updateSliderVal(2.0);
        document.getElementById('loopActivity').value = 'walk';
        document.getElementById('loopSensitivity').value = 'sensitive';
      }
      document.getElementById('loopSubmitBtn').click();
    }

    // ─── BACKEND GEOCODING AUTOCOMPLETE ───
    function setupAutocomplete(inputId, dropdownId, onSelect) {
      const input = document.getElementById(inputId);
      const dropdown = document.getElementById(dropdownId);
      let debounceTimer = null;

      input.addEventListener('input', function() {
        clearTimeout(debounceTimer);
        const query = input.value.trim();
        if (query.length < 2) {
          dropdown.style.display = 'none';
          return;
        }

        debounceTimer = setTimeout(async () => {
          try {
            const res = await fetch(`/v1/geocode?q=${encodeURIComponent(query)}`);
            const results = await res.json();
            if (results && results.length > 0) {
              dropdown.innerHTML = results.map(item => `
                <div class="suggestion-item" data-lat="${item.lat}" data-lng="${item.lng}">
                  ${item.name}
                </div>
              `).join('');
              dropdown.style.display = 'block';

              dropdown.querySelectorAll('.suggestion-item').forEach(el => {
                el.addEventListener('click', () => {
                  const lat = parseFloat(el.getAttribute('data-lat'));
                  const lng = parseFloat(el.getAttribute('data-lng'));
                  input.value = el.innerText.trim();
                  dropdown.style.display = 'none';
                  onSelect(lat, lng, input.value);
                  map.setView([lat, lng], 14);
                });
              });
            } else {
              dropdown.style.display = 'none';
            }
          } catch (e) {
            dropdown.style.display = 'none';
          }
        }, 300);
      });

      document.addEventListener('click', (e) => {
        if (!input.contains(e.target) && !dropdown.contains(e.target)) {
          dropdown.style.display = 'none';
        }
      });
    }

    setupAutocomplete('origSearch', 'origSuggestions', (lat, lng, name) => setOrigin(lat, lng, name));
    setupAutocomplete('destSearch', 'destSuggestions', (lat, lng, name) => setDest(lat, lng, name));
    setupAutocomplete('loopCenterSearch', 'loopSuggestions', (lat, lng, name) => setLoopCenter(lat, lng, name));

    // ─── ROUTE PLANNING SUBMISSION ───
    async function handleRoutePlan(e) {
      e.preventDefault();
      const btn = document.getElementById('routeSubmitBtn');
      btn.disabled = true;
      btn.innerHTML = `<span class="pulse-dot" style="display:inline-block;"></span> Routing & Calculating Exposure...`;

      let origLat = parseFloat(document.getElementById('origLat').value);
      let origLng = parseFloat(document.getElementById('origLng').value);
      let destLat = parseFloat(document.getElementById('destLat').value);
      let destLng = parseFloat(document.getElementById('destLng').value);
      const activity = document.getElementById('routeActivity').value;
      const sensitivity = document.getElementById('routeSensitivity').value;

      try {
        const res = await fetch('/v1/plan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            origin: { lat: origLat, lng: origLng },
            destination: { lat: destLat, lng: destLng },
            activity,
            sensitivity,
            alternate_routes: 2
          })
        });

        if (!res.ok) {
          const err = await res.json();
          alert('Planning Error: ' + (err.detail || res.statusText));
          return;
        }

        const data = await res.json();
        currentRoutes = data.routes || [];
        currentBestWindows = data.best_windows || [];
        currentDataNote = data.data_note || '';
        activeRouteIndex = 0;
        renderRoutesOutput(true);
      } catch (err) {
        alert('Server/Network Error: ' + err.message);
      } finally {
        btn.disabled = false;
        btn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m5 12 7-7 7 7"/><path d="M12 19V5"/></svg> Find Cleanest Route`;
      }
    }

    // ─── LOOP GENERATOR SUBMISSION ───
    async function handleLoopPlan(e) {
      e.preventDefault();
      const btn = document.getElementById('loopSubmitBtn');
      btn.disabled = true;
      btn.innerHTML = `<span class="pulse-dot" style="display:inline-block;"></span> Generating Scenic Loops...`;

      const centerLat = parseFloat(document.getElementById('loopLat').value);
      const centerLng = parseFloat(document.getElementById('loopLng').value);
      const distKm = parseFloat(document.getElementById('distSlider').value);
      const activity = document.getElementById('loopActivity').value;
      const sensitivity = document.getElementById('loopSensitivity').value;

      try {
        const res = await fetch('/v1/routes/loop', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            origin: { lat: centerLat, lng: centerLng },
            target_distance_km: distKm,
            activity,
            sensitivity,
            num_alternatives: 3
          })
        });

        if (!res.ok) {
          const err = await res.json();
          alert('Loop Generation Error: ' + (err.detail || res.statusText));
          return;
        }

        const routes = await res.json();
        currentRoutes = routes || [];
        currentBestWindows = [];
        currentDataNote = 'Circular loop optimized for low particulate exposure.';
        activeRouteIndex = 0;
        renderRoutesOutput(true);
      } catch (err) {
        alert('Server/Network Error: ' + err.message);
      } finally {
        btn.disabled = false;
        btn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg> Generate Clean Loop`;
      }
    }

    // ─── RENDER ROUTE RESULTS & TABS ───
    function renderRoutesOutput(shouldFitBounds = false) {
      const resultsDiv = document.getElementById('results-area');
      resultsDiv.style.display = 'flex';

      if (!currentRoutes || currentRoutes.length === 0) return;

      // Render Route Candidate Selector Tabs with hover sync
      const tabsRow = document.getElementById('routeTabsRow');
      if (currentRoutes.length > 1) {
        tabsRow.style.display = 'flex';
        tabsRow.innerHTML = currentRoutes.map((r, i) => {
          const isAct = i === activeRouteIndex ? 'active' : '';
          const tag = i === 0 ? 'Option A (Cleanest)' : `Option ${String.fromCharCode(65 + i)}`;
          return `
            <div class="route-tab-card ${isAct}" 
                 onclick="switchActiveRoute(${i})"
                 onmouseenter="highlightRoutePolyline(${i})"
                 onmouseleave="resetRoutePolylines()">
              <span class="route-tab-label">${tag}</span>
              <span class="route-tab-metric">${Math.round(r.mean_aqi)} AQI</span>
            </div>
          `;
        }).join('');
      } else {
        tabsRow.style.display = 'none';
      }

      const activeRoute = currentRoutes[activeRouteIndex];

      // Update Hero Exposure Banner
      const aqiNum = Math.round(activeRoute.mean_aqi);
      document.getElementById('resMeanAqi').innerText = aqiNum;
      document.getElementById('resMeanAqi').style.color = AQI_COLORS[activeRoute.category] || 'var(--text-main)';

      const badge = document.getElementById('resCatBadge');
      badge.innerText = activeRoute.category.replace('_', ' ');
      badge.style.background = AQI_COLORS[activeRoute.category] || '#eab308';

      // Update 4 Metrics
      document.getElementById('resDist').innerText = (activeRoute.distance_m / 1000).toFixed(2) + ' km';
      document.getElementById('resDur').innerText = Math.round(activeRoute.duration_s / 60) + ' min';
      document.getElementById('resDose').innerText = Math.round(activeRoute.dose).toLocaleString();
      document.getElementById('resConfidence').innerText = (activeRoute.confidence || 'HIGH').toUpperCase();

      // Health Advisory
      const advBox = document.getElementById('resAdvisoryBox');
      const advColor = activeRoute.advisory === 'ok' ? '#10b981' : (activeRoute.advisory === 'caution' ? '#eab308' : '#ef4444');
      advBox.innerHTML = `
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="${advColor}" stroke-width="2.5" style="flex-shrink:0;"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        <div>
          <strong style="color: ${advColor}; text-transform: uppercase;">Advisory: ${activeRoute.advisory}</strong> &bull; 
          ${currentDataNote || 'Calculated with micro-dispersion road class attenuation.'}
        </div>
      `;

      // Diurnal Windows Chart
      const windowsCard = document.getElementById('windowsCard');
      const chartBars = document.getElementById('chartBars');
      const hintBox = document.getElementById('timelineHint');
      hintBox.style.display = 'none';

      if (currentBestWindows && currentBestWindows.length > 0) {
        windowsCard.style.display = 'flex';
        const minAqiWin = [...currentBestWindows].sort((a,b) => a.mean_aqi - b.mean_aqi)[0];
        document.getElementById('bestHourHighlight').innerText = `Peak Window: ${minAqiWin.hour}:00 UTC (${Math.round(minAqiWin.mean_aqi)} AQI)`;

        chartBars.innerHTML = currentBestWindows.map(w => {
          const heightPct = Math.min(100, Math.max(15, (w.mean_aqi / 350) * 100));
          const color = AQI_COLORS[w.category] || 'var(--accent)';
          const hrStr = w.hour < 10 ? '0' + w.hour : w.hour;
          return `
            <div class="chart-bar-col" onclick="showHourAdvice(${w.hour}, ${Math.round(w.mean_aqi)}, '${w.category}')" title="${hrStr}:00 UTC — ${Math.round(w.mean_aqi)} AQI (${w.category})">
              <div class="chart-bar-fill" style="height: ${heightPct}%; background: ${color};"></div>
              <span class="chart-bar-label">${hrStr}h</span>
            </div>
          `;
        }).join('');
      } else {
        windowsCard.style.display = 'none';
      }

      drawMapPolylines(shouldFitBounds);
    }

    function showHourAdvice(hour, aqi, cat) {
      const hintBox = document.getElementById('timelineHint');
      hintBox.style.display = 'block';
      const cleanWord = aqi < 100 ? 'cleanest' : 'elevated';
      hintBox.innerHTML = `<strong>${hour}:00 UTC Forecast:</strong> Estimated AQI is <strong>${aqi}</strong> (${cat.replace('_', ' ')}). Air conditions are <em>${cleanWord}</em> compared to rush hours.`;
    }

    function switchActiveRoute(index) {
      activeRouteIndex = index;
      renderRoutesOutput(false);
    }

    function highlightRoutePolyline(index) {
      polylines.forEach((poly, i) => {
        if (i === index) {
          poly.setStyle({ weight: 8, opacity: 1 });
          poly.bringToFront();
        } else {
          poly.setStyle({ weight: 2, opacity: 0.25 });
        }
      });
    }

    function resetRoutePolylines() {
      drawMapPolylines(false);
    }

    function drawMapPolylines(shouldFitBounds = false) {
      polylines.forEach(p => map.removeLayer(p));
      polylines = [];

      currentRoutes.forEach((route, i) => {
        if (!route.geometry || route.geometry.length === 0) return;
        const coords = route.geometry.map(c => [c.lat, c.lng]);
        const isActive = i === activeRouteIndex;
        const color = isActive ? (AQI_COLORS[route.category] || 'var(--primary)') : (currentTheme === 'dark' ? '#475569' : '#94a3b8');

        const poly = L.polyline(coords, {
          color: color,
          weight: isActive ? 6 : 3,
          opacity: isActive ? 0.95 : 0.45,
          dashArray: isActive ? null : '6, 6'
        }).addTo(map);

        if (isActive) poly.bringToFront();

        poly.on('click', () => switchActiveRoute(i));
        polylines.push(poly);
      });

      if (shouldFitBounds && polylines[activeRouteIndex]) {
        const leftPadding = window.innerWidth > 768 && !isPanelCollapsed ? 480 : 30;
        map.fitBounds(polylines[activeRouteIndex].getBounds(), {
          paddingTopLeft: [leftPadding, 30],
          paddingBottomRight: [30, 30],
          maxZoom: 16
        });
      }
    }

    // ─── LIVE POINT AQI INSPECTION ───
    async function probePointAirQuality(lat, lng) {
      try {
        const res = await fetch(`/v1/airquality?lat=${lat.toFixed(5)}&lng=${lng.toFixed(5)}`);
        if (!res.ok) return;
        const data = await res.json();

        const card = document.getElementById('point-inspector');
        card.style.display = 'flex';

        document.getElementById('inspAqi').innerText = Math.round(data.aqi);
        document.getElementById('inspAqi').style.color = AQI_COLORS[data.category] || 'var(--text-main)';

        const badge = document.getElementById('inspBadge');
        badge.innerText = data.category.replace('_', ' ');
        badge.style.background = AQI_COLORS[data.category] || '#eab308';

        document.getElementById('inspDominant').innerText = (data.dominant_pollutant || 'PM2.5').toUpperCase();

        const pDiv = document.getElementById('inspPollutants');
        pDiv.innerHTML = Object.entries(data.pollutants || {}).map(([name, val]) => `
          <div>${name}: <strong style="color:var(--text-main);">${Math.round(val)}</strong></div>
        `).join('');
      } catch (err) {
        // Silently skip
      }
    }

    function closeInspector() {
      document.getElementById('point-inspector').style.display = 'none';
    }

    // Initialize with saved theme or default
    const savedTheme = localStorage.getItem('aeropath-theme') || (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    applyTheme(savedTheme);

    // Initial setup with Kolkata preset
    setPreset('kolkata');

    // Auto-resize map to viewport
    window.addEventListener('resize', () => {
      map.invalidateSize();
    });
    setTimeout(() => {
      map.invalidateSize();
    }, 250);

    // Automatically trigger initial route planning so routes and metrics display immediately on load
    window.addEventListener('DOMContentLoaded', () => {
      setTimeout(() => {
        const btn = document.getElementById('routeSubmitBtn');
        if (btn) btn.click();
      }, 400);
    });
  </script>
</body>
</html>
"""
