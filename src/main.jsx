import React from 'react';
import { createRoot } from 'react-dom/client';
import AiSignalWidget from '../bist_terminal/components/AiSignalWidget.jsx';
import MetricQualityPanel from '../bist_terminal/components/MetricQualityPanel.jsx';

const signalsEl = document.getElementById('ai-signals-root');
if (signalsEl) {
  createRoot(signalsEl).render(
    <AiSignalWidget signalsUrl="/data/signals.json" />
  );
}

const qualityEl = document.getElementById('metric-quality-root');
if (qualityEl) {
  createRoot(qualityEl).render(
    <MetricQualityPanel qualityUrl="/data/metric_quality_report.json" />
  );
}
