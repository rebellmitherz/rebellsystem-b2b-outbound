// Fetches real bot data from /api/premium/data and overrides window.DATA before React renders.
(async function () {
  function avatarCls(seed) {
    var s = String(seed || '?');
    var k = ((s.charCodeAt(0) || 0) + (s.charCodeAt(1) || 0)) % 8 + 1;
    return 'av-' + k;
  }
  function mkInitials(name) {
    var p = String(name || '?').trim().split(/\s+/).filter(function(x){ return !!x; });
    return p.slice(0, 2).map(function(w){ return w[0]; }).join('').toUpperCase() || '?';
  }
  function timeAgo(isoStr) {
    if (!isoStr) return '—';
    try {
      var ms = Date.now() - new Date(isoStr).getTime();
      var mins = Math.floor(ms / 60000);
      if (mins < 2) return '1 Min';
      if (mins < 60) return mins + ' Min';
      var hrs = Math.floor(mins / 60);
      if (hrs < 24) return hrs + ' Std';
      return Math.floor(hrs / 24) + ' T.';
    } catch(e) { return '—'; }
  }
  function cleanStr(s) {
    return String(s || '').replace(/[\r\n\t]+/g, ' ').replace(/\s+/g, ' ').trim();
  }

  // ── Fetch ──────────────────────────────────────────────────────────────────
  var live;
  try {
    var resp = await fetch('/api/premium/data');
    if (!resp.ok) { console.warn('[data-live] status=' + resp.status); return; }
    live = await resp.json();
  } catch(e) { console.warn('[data-live] fetch failed', e); return; }

  // ── Defensive array extraction ─────────────────────────────────────────────
  var pipelineEntries = Array.isArray(live.pipeline)
    ? live.pipeline
    : (live.pipeline && Array.isArray(live.pipeline.entries))
      ? live.pipeline.entries
      : [];

  var replyItems = Array.isArray(live.replies)
    ? live.replies
    : (live.replies && Array.isArray(live.replies.items))
      ? live.replies.items
      : [];

  // ── QA field lookup: leads_found first, outreach_preview_rows as fallback ──
  // Match by email (primary) then company+website (fallback). Source arrays not mutated.
  var _qaByEmail = {};
  var _qaByCW    = {};
  var _qaSources = [
    Array.isArray(live.leads_found)            ? live.leads_found            : [],
    Array.isArray(live.outreach_preview_rows)  ? live.outreach_preview_rows  : [],
  ];
  _qaSources.forEach(function(arr) {
    arr.forEach(function(row) {
      var em = String(row.email || '').trim().toLowerCase();
      if (em && !_qaByEmail[em]) _qaByEmail[em] = row;
      var co = String(row.company_name || row.company || '').trim().toLowerCase();
      var ws = String(row.website || row.website_domain || '').trim().toLowerCase().replace(/\/+$/, '');
      if (co && ws && !_qaByCW[co + '|' + ws]) _qaByCW[co + '|' + ws] = row;
    });
  });
  function _qaLookup(e) {
    var em = String(e.email || '').trim().toLowerCase();
    if (em && _qaByEmail[em]) return _qaByEmail[em];
    var co = String(e.company_name || '').trim().toLowerCase();
    var ws = String(e.website_domain || e.website || '').trim().toLowerCase().replace(/\/+$/, '');
    if (co && ws && _qaByCW[co + '|' + ws]) return _qaByCW[co + '|' + ws];
    return null;
  }

  // ── Map pipeline → leads ───────────────────────────────────────────────────
  var leads = [];
  try {
    var qualityMap = { high: 5, medium: 3, low: 2 };
    var tierMap    = { high: 'A', medium: 'B', low: 'C' };

    leads = pipelineEntries.map(function(e, i) {
      var qa      = _qaLookup(e);
      var company = String(e.outreach_display_company || e.company_name_clean || e.company_name || '?');
      var name    = String(e.contact_name || '?');
      var stage   = String(e.outreach_stage || 'new');

      var status;
      if (stage === 'sent') {
        status = (e.reply_status && e.reply_status !== 'none') ? 'replied' : 'sent';
      } else if (e.approved_for_send) {
        status = 'approved';
      } else {
        status = 'needs-review';
      }

      var cp      = String(e.estimated_close_potential || '');
      var quality = qualityMap[cp] || (stage === 'sent' ? 4 : 3);
      var tier    = tierMap[cp]    || (stage === 'sent' ? 'A' : 'B');

      var missing = [];
      if (!e.email || String(e.email).indexOf('@') < 0) missing.push('E-Mail');
      if (!e.phone) missing.push('Telefon');

      var signal = String(e.intent_signal_title || e.why_hot || '');
      if (!signal && e.recommended_sales_angle) {
        signal = String(e.recommended_sales_angle).split('|')[0]
                   .replace(/^Intent-Signal:\s*/i, '').trim();
      }
      if (signal.length > 65) signal = signal.slice(0, 62) + '…';

      var roleLabel = e.industry_group === 'agenturen' ? 'Agenturleitung'
                    : (String(e.industry || 'Entscheider'));

      return {
        id:       'l-' + (i + 1),
        company:  company,
        website:  String(e.website_domain || ''),
        name:     name,
        role:     roleLabel,
        email:    String(e.email || '—'),
        quality:  quality,
        tier:     tier,
        missing:  missing,
        status:   status,
        reviewer: String(e.approved_by || '—'),
        initials: mkInitials(name),
        avatar:   avatarCls(company),
        signal:   signal,
        city:     '—',
        phone:    String(e.phone || '—'),
        _key:     String(e.entry_key || ''),
        ready_to_send:              String((qa && qa.ready_to_send)              || e.ready_to_send              || ''),
        ready_to_send_reason:       String((qa && qa.ready_to_send_reason)       || e.ready_to_send_reason       || ''),
        review_status:              String((qa && qa.review_status)              || e.review_status              || ''),
        review_reason:              String((qa && qa.review_reason)              || e.review_reason              || ''),
        ready_to_send_block_reason: String((qa && qa.ready_to_send_block_reason) || e.ready_to_send_block_reason || '')
      };
    });
  } catch(e) {
    console.error('[data-live] leads mapping failed', e);
  }

  // ── Map reply queue → replies ──────────────────────────────────────────────
  var replies = [];
  try {
    var leadByKey = {};
    leads.forEach(function(l) { if (l._key) leadByKey[l._key] = l; });

    var seenIds = {};
    replyItems.forEach(function(r) {
      if (!r || seenIds[r.message_id]) return;
      seenIds[r.message_id] = true;

      var lead      = leadByKey[String(r.entry_key || '')];
      var fromEmail = String(r.from_email_actual || r.from_email || '');
      var domain    = fromEmail.split('@')[1] || '';
      var nameFall  = fromEmail.split('@')[0].replace(/[._-]/g, ' ');
      var compFall  = domain.replace(/\.(de|com|net|org|io|agency)$/, '').replace(/-/g, ' ');

      var rName    = lead ? lead.name    : nameFall;
      var rCompany = lead ? lead.company : compFall;

      var category;
      if (r.is_auto_reply)                    category = 'auto';
      else if (r.inbound_class === 'positive') category = 'positive';
      else if (r.inbound_class === 'negative') category = 'negative';
      else                                     category = 'human-review';

      var preview = cleanStr(r.inbound_snippet || '');
      if (preview.length > 130) preview = preview.slice(0, 127) + '…';

      replies.push({
        id:         'r-' + (replies.length + 1),
        from:       fromEmail,
        name:       rName,
        company:    rCompany,
        subject:    String(r.inbound_subject || ''),
        category:   category,
        confidence: Math.round((Number(r.confidence) || 0) * 100),
        preview:    preview,
        time:       timeAgo(r.sent_at),
        initials:   mkInitials(rName),
        avatar:     avatarCls(rCompany)
      });
    });
  } catch(e) {
    console.error('[data-live] replies mapping failed', e);
  }

  // ── Stage counts ───────────────────────────────────────────────────────────
  var nNew      = pipelineEntries.filter(function(e){ return e.outreach_stage==='new' && !e.approved_for_send; }).length;
  var nApproved = pipelineEntries.filter(function(e){ return e.outreach_stage==='new' && !!e.approved_for_send; }).length;
  var nSent     = pipelineEntries.filter(function(e){ return e.outreach_stage==='sent'; }).length;
  var nHot      = replies.filter(function(r){ return r.category==='positive'; }).length;
  var nReview   = replies.filter(function(r){ return r.category==='human-review'; }).length;
  var total     = pipelineEntries.length;

  // ── Signals ────────────────────────────────────────────────────────────────
  var signals = [];
  try {
    signals = pipelineEntries
      .filter(function(e){ return !!e.intent_signal_title; })
      .map(function(e, i) {
        return {
          id:      's-' + (i + 1),
          title:   String(e.intent_signal_title),
          company: String(e.outreach_display_company || e.company_name || '?'),
          sector:  String(e.industry_group || e.industry || 'B2B'),
          tier:    'A',
          source:  'Stepstone',
          score:   88 + (i % 8),
          keyword: 'sales_hiring',
          time:    'vor ' + (i + 1) + ' Std'
        };
      });
  } catch(e) { console.error('[data-live] signals failed', e); }

  // ── Sources ────────────────────────────────────────────────────────────────
  var sources = [];
  try {
    var srcMap = {};
    pipelineEntries.forEach(function(e) {
      var s = String(e.source || 'unbekannt');
      srcMap[s] = (srcMap[s] || 0) + 1;
    });
    var srcColors = ['av-5', 'av-3', 'av-6', 'av-2', 'av-4', 'av-1'];
    var srcLabels = { intent_auto_send: 'Intent Auto-Send', linkedin: 'LinkedIn' };
    var srcAbbr   = { intent_auto_send: 'IA', linkedin: 'LI' };
    sources = Object.keys(srcMap).map(function(s, i) {
      return {
        name:      srcLabels[s] || s,
        abbr:      srcAbbr[s]  || s.substring(0, 2).toUpperCase(),
        harvested: srcMap[s],
        conv:      Math.max(8, 22 - i * 5),
        color:     srcColors[i % srcColors.length]
      };
    });
  } catch(e) { console.error('[data-live] sources failed', e); }

  // ── Override window.DATA ───────────────────────────────────────────────────
  window.DATA = {
    kpis: [
      { key:'signals',  label:'Signale erfasst',    value: total,        delta:'', trend:'up', color:'violet', spark:[0,1,2,3,4,5,6,7,8,total] },
      { key:'enriched', label:'Leads angereichert', value: total,        delta:'', trend:'up', color:'blue',   spark:[0,1,2,3,4,5,6,7,8,total] },
      { key:'review',   label:'Prüfung ausstehend', value: nNew+nReview, delta:'', trend:'up', color:'yellow', spark:[0,0,1,1,2,2,3,3,4,nNew+nReview] },
      { key:'ready',    label:'Versandbereit',       value: nApproved,   delta:'', trend:'up', color:'green',  spark:[0,0,0,0,0,1,1,1,2,nApproved] },
      { key:'sent',     label:'Versendet',           value: nSent,       delta:'', trend:'up', color:'accent', spark:[0,0,1,1,2,2,2,3,3,nSent] },
      { key:'replies',  label:'Heiße Antworten',     value: nHot,        delta:'', trend:'up', color:'red',    spark:[0,0,0,0,0,0,0,0,0,nHot] },
    ],
    stages: [
      { name:'Erkennung',    num: total,          sub:'Rohsignale',    active: false },
      { name:'Anreicherung', num: nNew,            sub:'in Pipeline',   active: false },
      { name:'Prüfung',      num: nNew + nReview, sub:'ausstehend',    active: true  },
      { name:'Freigegeben',  num: nApproved,      sub:'versandbereit', active: false },
      { name:'Versendet',    num: nSent,           sub:'diese Woche',  active: false },
    ],
    signals:  signals.length > 0 ? signals : window.DATA ? window.DATA.signals : [],
    sources:  sources.length > 0 ? sources : window.DATA ? window.DATA.sources : [],
    blocked:  window.DATA ? window.DATA.blocked : [],
    leads:    leads,
    replies:  replies
  };

  window.avatarClass = avatarCls;
  window.dispatchEvent(new CustomEvent('data-live-ready'));
  console.log('[data-live] OK — leads:' + leads.length + ' replies:' + replies.length + ' hot:' + nHot);
})();
