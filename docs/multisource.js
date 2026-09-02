(() => {
  const originalDetailHtml = window.detailHtml;
  if (typeof originalDetailHtml !== 'function') return;

  const sourcePanel = (r) => {
    const a = r.analysis || {};
    const sources = Array.isArray(a.valuation_sources) ? a.valuation_sources : [];
    if (!sources.length && a.quick_sale_value == null) return '';
    const agreement = a.source_agreement_percent == null ? '—' : `${a.source_agreement_percent}%`;
    const rows = sources.map(s => `
      <div class="sourceRow">
        <div><b>${esc(s.source || 'Bron')}</b><small>${esc(s.kind || '')}${s.samples ? ` · ${s.samples} samples` : ''}</small></div>
        <strong>${euro(s.value)}</strong>
      </div>`).join('');
    return `
      <section class="multiValue panel">
        <div class="cardtop"><div><span class="badge info">MULTI-SOURCE</span></div><b>Bronovereenkomst ${agreement}</b></div>
        <h3>Waarde-indicatie</h3>
        <div class="valueTriplet">
          <div><small>Snelle verkoop</small><strong>${euro(a.quick_sale_value ?? a.market_low)}</strong></div>
          <div class="realistic"><small>Realistische waarde</small><strong>${euro(a.realistic_market_value ?? a.expected_resale)}</strong></div>
          <div><small>Optimistisch</small><strong>${euro(a.optimistic_value ?? a.market_high)}</strong></div>
        </div>
        ${rows ? `<div class="sourceRows">${rows}</div>` : ''}
        <div class="small">Vraagprijzen zijn marktindicaties en geen garantie voor een gerealiseerde verkoopprijs.</div>
      </section>`;
  };

  window.detailHtml = function(r) {
    const base = originalDetailHtml(r);
    const panel = sourcePanel(r);
    if (!panel) return base;
    const marker = '<div class="detailGrid">';
    return base.includes(marker) ? base.replace(marker, panel + marker) : panel + base;
  };

  const style = document.createElement('style');
  style.textContent = `
    .multiValue{margin:14px 0;background:linear-gradient(135deg,rgba(41,121,255,.10),rgba(52,211,153,.07));}
    .valueTriplet{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0;}
    .valueTriplet>div{padding:12px;border:1px solid var(--line,#26313c);border-radius:12px;background:rgba(255,255,255,.025);}
    .valueTriplet small,.sourceRow small{display:block;opacity:.68;margin-bottom:4px;}
    .valueTriplet strong{font-size:1.25rem;}
    .valueTriplet .realistic{border-color:rgba(52,211,153,.5);}
    .sourceRows{display:grid;gap:6px;margin:12px 0;}
    .sourceRow{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:9px 0;border-top:1px solid var(--line,#26313c);}
    .sourceRow strong{white-space:nowrap;}
    @media(max-width:640px){.valueTriplet{grid-template-columns:1fr}.multiValue{margin-left:0;margin-right:0}}
  `;
  document.head.appendChild(style);
})();
