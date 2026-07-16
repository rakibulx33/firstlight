    function app() {
      // Kept OUT of the returned object on purpose: a Chart.js instance has circular
      // refs and breaks if Alpine wraps it in a reactive proxy ("Maximum call stack
      // size exceeded" / "fullSize" errors on chart.update()).
      let _chart = null;
      return {
        connected: false, busy: false,
        status: { running:false, uptime_s:null, last_poll_ts:null, last_latency_ms:null, markets_count:0, poll_count:0, error_count:0, poll_interval:1.0, last_error:null },
        exchangeStatuses: {}, // {binance:{...}, bithumb:{...}, coinbase:{...}} — keyed status events, upbit stays in `status`
        fastFeed: { configured:false, running:false, connected:{tokyo:false,seoul:false}, message_count:0, listing_count:0, error_count:0, last_message_ts:null, last_error:null },
        listings: [], logs: [], markets: [], marketFilter: '', exchangeFilter: 'all', notices: [],
        subscribers: [], newSub: { chat_id:'', name:'', tier:'free' }, subError: null,
        tab: 'live', tabs: [{id:'live',label:'Live'},{id:'announce',label:'Announce'},{id:'markets',label:'Markets'},{id:'phase0',label:'Phase 0'},{id:'subscribers',label:'Subscribers'}],
        settingsOpen: false,
        settingsSection: 'detector',
        savedFlash: false,
        settings: {
          poll_interval:1.0, poll_interval_notice:3.0,
          poll_interval_binance:5.0, poll_interval_bithumb:5.0, poll_interval_coinbase:5.0,
          autostart:true,
          phase0_offsets:[0,10,30,60,300], phase0_offsets_str:'0, 10, 30, 60, 300',
          phase0_sources:{bybit:true, binance:true},
          notice_keywords:{listing:[], exclude:[]},
          kw_listing_str:'', kw_exclude_str:'',
          alert_on_listing:true, alert_on_notice:true, alert_on_error:false,
          quiet_hours:{enabled:false, start:'23:00', end:'07:00'},
          subscriber_tiers:{instant:0, delayed:30, free:120}, tiersRows:[],
          telegram_chat_id:'', telegram_token:'', telegram_token_set:false, telegram_configured:false,
          coinlisting_api_key:'', coinlisting_configured:false
        },
        tgTestResult: null,
        snapMarket: '', snapMarkets: [], snapshots: [],
        toast: null,
        prefs: { theme:'dark', accent:'amber', density:'comfortable', fontScale:'base',
                 defaultTab:'live', visibleTabs:['live','announce','markets','phase0','subscribers'],
                 visibleCards:['status','about','exchanges','fastfeed'], timeFormat:'local', tablePageSize:200,
                 numberFormat:{decimals:4, grouping:true}, favorites:[],
                 toastDuration:6000, toastSound:false, toastEvents:['listing','notice'] },
        loadPrefs(){ try{ this.prefs = { ...this.prefs, ...JSON.parse(localStorage.getItem('upbitwatch.prefs')||'{}') }; }catch(e){} },
        savePrefs(){ localStorage.setItem('upbitwatch.prefs', JSON.stringify(this.prefs)); },
        setPref(key, val){ this.prefs[key] = val; this.savePrefs(); this.applyPrefs(); },
        toggleInArray(arr, val){ const i=arr.indexOf(val); i>-1?arr.splice(i,1):arr.push(val); this.savePrefs(); this.applyPrefs(); },
        applyPrefs(){
          const root = document.documentElement;
          let theme = this.prefs.theme;
          if(theme==='system') theme = matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';
          root.setAttribute('data-theme', theme);
          root.setAttribute('data-accent', this.prefs.accent);
          root.setAttribute('data-density', this.prefs.density);
          root.setAttribute('data-fontscale', this.prefs.fontScale);
          if(_chart){ this.renderChart(); }   // re-tint chart axes/legend on theme change
        },

        async saveServerSettings(){
          this.tgTestResult = null;
          const s = this.settings;
          const tiers = {};
          for (const row of s.tiersRows) { if (row.name && row.name.trim()) tiers[row.name.trim()] = parseFloat(row.delay) || 0; }
          const body = {
            poll_interval: parseFloat(s.poll_interval),
            poll_interval_notice: parseFloat(s.poll_interval_notice),
            poll_interval_binance: parseFloat(s.poll_interval_binance),
            poll_interval_bithumb: parseFloat(s.poll_interval_bithumb),
            poll_interval_coinbase: parseFloat(s.poll_interval_coinbase),
            autostart: !!s.autostart,
            phase0_offsets: (''+s.phase0_offsets_str).split(',').map(x=>parseInt(x.trim(),10)).filter(n=>!isNaN(n)&&n>=0),
            phase0_sources: s.phase0_sources,
            notice_keywords: { listing: s.kw_listing_str.split(',').map(x=>x.trim()).filter(Boolean),
                               exclude: s.kw_exclude_str.split(',').map(x=>x.trim()).filter(Boolean) },
            alert_on_listing: !!s.alert_on_listing, alert_on_notice: !!s.alert_on_notice, alert_on_error: !!s.alert_on_error,
            quiet_hours: s.quiet_hours,
            subscriber_tiers: tiers,
          };
          if(s.telegram_token) body.telegram_token = s.telegram_token;
          if(s.telegram_chat_id != null && String(s.telegram_chat_id).trim() !== '') body.telegram_chat_id = s.telegram_chat_id;
          if(s.coinlisting_api_key) body.coinlisting_api_key = s.coinlisting_api_key;
          const res = await (await fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
          this.settings = { ...this.settings, ...res, telegram_token:'', coinlisting_api_key:'',
            phase0_offsets_str: (res.phase0_offsets||[]).join(', '),
            kw_listing_str: (res.notice_keywords?.listing||[]).join(', '),
            kw_exclude_str: (res.notice_keywords?.exclude||[]).join(', ') };
          if(!this.settings.phase0_sources || typeof this.settings.phase0_sources !== 'object') this.settings.phase0_sources = {bybit:true, binance:true};
          if(!this.settings.quiet_hours || typeof this.settings.quiet_hours !== 'object') this.settings.quiet_hours = {enabled:false, start:'23:00', end:'07:00'};
          if(!this.settings.subscriber_tiers || typeof this.settings.subscriber_tiers !== 'object') this.settings.subscriber_tiers = {instant:0, delayed:30, free:120};
          this.settings.tiersRows = Object.entries(this.settings.subscriber_tiers).map(([name, delay]) => ({ name, delay }));
          this.savedFlash = true; setTimeout(()=>this.savedFlash=false, 1600);
        },

        async init() {
          this.loadPrefs(); this.applyPrefs();
          this.tab = this.prefs.defaultTab || 'live';
          this.connectWS();
          await this.refreshAll();
          this.icons();
        },
        icons() { this.$nextTick(() => window.lucide && window.lucide.createIcons()); },

        connectWS() {
          const proto = location.protocol === 'https:' ? 'wss' : 'ws';
          let ws;
          try { ws = new WebSocket(`${proto}://${location.host}/ws`); }
          catch (e) { this.connected = false; setTimeout(() => this.connectWS(), 1500); return; }
          ws.onopen = () => { this.connected = true; };
          ws.onclose = () => { this.connected = false; setTimeout(() => this.connectWS(), 1500); };
          ws.onerror = () => { try { ws.close(); } catch (e) {} };
          ws.onmessage = (e) => { try { this.onEvent(JSON.parse(e.data)); } catch (err) {} };
        },
        onEvent(ev) {
          // Every exchange's DetectorEngine publishes "status" on the same bus (tagged
          // with ev.data.exchange) — only Upbit drives the main status bar, the other
          // three route into exchangeStatuses so they don't clobber each other.
          if (ev.type === 'status') {
            if (ev.data.exchange && ev.data.exchange !== 'upbit') this.exchangeStatuses[ev.data.exchange] = ev.data;
            else this.status = ev.data;
          }
          else if (ev.type === 'log') { this.logs.push(ev.data); if (this.logs.length > 300) this.logs.splice(0, this.logs.length - 300); this.scrollLogs(); }
          else if (ev.type === 'listing') {
            if (this.exchangeFilter === 'all' || this.exchangeFilter === ev.data.exchange) this.listings.unshift(ev.data);
            this.showToast(ev.data, 'listing'); this.icons();
          }
          else if (ev.type === 'notice') { this.notices.unshift(ev.data); if (ev.data.is_listing) this.showToast(ev.data, 'notice'); this.icons(); }
          else if (ev.type === 'snapshot') {
            const key = ev.data.exchange + '|' + ev.data.market;
            if (this.tab === 'phase0' && !this.snapMarkets.some(m => m.exchange === ev.data.exchange && m.market === ev.data.market)) this.loadSnapshotMarkets();
            if (key === this.snapMarket) this.loadSnapshots();
          }
        },

        async refreshAll() { await Promise.all([this.loadStatus(), this.loadExchanges(), this.loadFastFeed(), this.loadListings(), this.loadNotices(), this.loadLogs(), this.loadMarkets(), this.loadSettings(), this.loadSubscribers()]); },
        async loadStatus() { this.status = await (await fetch('/api/status')).json(); },
        async loadExchanges() {
          const rows = await (await fetch('/api/exchanges')).json();
          for (const r of rows) if (r.exchange !== 'upbit') this.exchangeStatuses[r.exchange] = r;
        },
        async loadFastFeed() { this.fastFeed = await (await fetch('/api/fastfeed')).json(); },
        async fastFeedCtrl(action) {
          this.busy = true;
          try { const r = await fetch('/api/fastfeed/' + action, { method:'POST' }); const j = await r.json(); if (j.status) this.fastFeed = j.status; }
          finally { this.busy = false; }
        },
        async loadListings() {
          const q = this.exchangeFilter !== 'all' ? ('?exchange=' + encodeURIComponent(this.exchangeFilter)) : '';
          this.listings = await (await fetch('/api/listings' + q)).json();
        },
        async loadLogs() { this.logs = await (await fetch('/api/logs')).json(); this.scrollLogs(); },
        async loadMarkets() {
          const q = this.exchangeFilter !== 'all' ? ('?exchange=' + encodeURIComponent(this.exchangeFilter)) : '';
          this.markets = await (await fetch('/api/markets' + q)).json();
        },
        async setExchangeFilter(id) { this.exchangeFilter = id; await Promise.all([this.loadListings(), this.loadMarkets()]); },
        async loadNotices() { this.notices = await (await fetch('/api/notices')).json(); },
        async loadSubscribers() { this.subscribers = await (await fetch('/api/subscribers')).json(); },
        async addSubscriber() {
          this.subError = null;
          const r = await fetch('/api/subscribers', { method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify(this.newSub) });
          const j = await r.json();
          if (!r.ok) { this.subError = j.detail || 'Failed to add subscriber'; return; }
          this.newSub = { chat_id:'', name:'', tier:'free' };
          await this.loadSubscribers();
        },
        async updateSubscriber(sub, fields) {
          await fetch('/api/subscribers/' + sub.id, { method:'PUT', headers:{'Content-Type':'application/json'},
            body: JSON.stringify(fields) });
          await this.loadSubscribers();
        },
        async deleteSubscriber(sub) {
          if (!confirm('Remove subscriber ' + (sub.name || sub.chat_id) + '?')) return;
          await fetch('/api/subscribers/' + sub.id, { method:'DELETE' });
          await this.loadSubscribers();
        },
        async exchangeCtrl(id, action) {
          this.busy = true;
          try {
            const r = await fetch('/api/exchanges/' + id + '/' + action, { method:'POST' });
            const j = await r.json();
            if (j.status) this.exchangeStatuses[id] = j.status;
          } finally { this.busy = false; this.icons(); }
        },
        async exchangeSimulate(id) { await fetch('/api/exchanges/' + id + '/simulate', { method:'POST' }); setTimeout(() => { this.loadSnapshotMarkets(); }, 1500); },
        async loadSettings() {
          const s = await (await fetch('/api/settings')).json();
          this.settings = { ...this.settings, ...s, telegram_token: '', coinlisting_api_key: '' };
          // Ensure nested objects are proper objects after merge
          if(!this.settings.phase0_sources || typeof this.settings.phase0_sources !== 'object') {
            this.settings.phase0_sources = {bybit:true, binance:true};
          }
          if(!this.settings.quiet_hours || typeof this.settings.quiet_hours !== 'object') {
            this.settings.quiet_hours = {enabled:false, start:'23:00', end:'07:00'};
          }
          if(!this.settings.subscriber_tiers || typeof this.settings.subscriber_tiers !== 'object') {
            this.settings.subscriber_tiers = {instant:0, delayed:30, free:120};
          }
          // Populate string mirrors for comma-separated fields
          this.settings.phase0_offsets_str = (s.phase0_offsets||[]).join(', ');
          this.settings.kw_listing_str = (s.notice_keywords?.listing||[]).join(', ');
          this.settings.kw_exclude_str = (s.notice_keywords?.exclude||[]).join(', ');
          this.settings.tiersRows = Object.entries(this.settings.subscriber_tiers).map(([name, delay]) => ({ name, delay }));
        },
        addTierRow() { this.settings.tiersRows.push({ name:'', delay:0 }); },
        removeTierRow(i) { this.settings.tiersRows.splice(i, 1); },

        get filteredMarkets(){ const f=this.marketFilter.trim().toUpperCase();
          let list = f ? this.markets.filter(m=>m.market.includes(f)) : this.markets.slice();
          const fav = this.prefs.favorites;
          return list.sort((a,b)=>(fav.includes(b.market)?1:0)-(fav.includes(a.market)?1:0)); },
        get pagedMarkets(){ return this.filteredMarkets.slice(0, this.prefs.tablePageSize); },
        fmtNum(v){ if(v==null || isNaN(Number(v))) return '—'; const n=Number(v);
          return n.toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:this.prefs.numberFormat.decimals,useGrouping:this.prefs.numberFormat.grouping}); },
        toggleFavorite(market){ this.toggleInArray(this.prefs.favorites, market); },

        switchTab(id) { this.tab = id; this.icons(); if (id === 'phase0') { this.loadSnapshotMarkets().then(() => this.loadSnapshots()); } if (id === 'announce') this.loadNotices(); },

        async ctrl(action) {
          this.busy = true;
          try { const j = await (await fetch('/api/' + action, { method: 'POST' })).json(); if (j.status) this.status = j.status; }
          finally { this.busy = false; this.icons(); }
        },
        stopConfirm() { if (confirm('Stop the detector?')) this.ctrl('stop'); },

        openSettings() {
          this._settingsTrigger = document.activeElement;
          this.settingsOpen = true;
          this.loadSettings();
          this.icons();
          this.$nextTick(() => {
            const focusable = this._panelFocusables();
            if (focusable.length) focusable[0].focus();
          });
        },
        closeSettings() {
          this.settingsOpen = false;
          this.$nextTick(() => {
            if (this._settingsTrigger && this._settingsTrigger.focus) this._settingsTrigger.focus();
          });
        },
        _panelFocusables() {
          const panel = this.$refs.settingsPanel;
          if (!panel) return [];
          return Array.from(panel.querySelectorAll('button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])'))
            .filter(el => !el.disabled && el.offsetParent !== null);
        },
        trapFocus(e) {
          const focusable = this._panelFocusables();
          if (!focusable.length) return;
          const first = focusable[0], last = focusable[focusable.length - 1];
          if (e.shiftKey) { if (document.activeElement === first) { e.preventDefault(); last.focus(); } }
          else { if (document.activeElement === last) { e.preventDefault(); first.focus(); } }
        },

        async telegramTest() {
          this.tgTestResult = 'sending';
          const r = await (await fetch('/api/telegram/test', { method: 'POST' })).json();
          this.tgTestResult = r.ok ? 'ok' : ('fail: ' + JSON.stringify(r.result));
        },
        async simulate() { await fetch('/api/simulate', { method: 'POST' }); setTimeout(() => { this.loadSnapshotMarkets(); this.loadSnapshots(); }, 1500); },

        async loadSnapshotMarkets() {
          // Each entry is {exchange, market}; the <select> is keyed by "exchange|market"
          // since the same market string can legitimately exist on more than one exchange.
          this.snapMarkets = await (await fetch('/api/snapshots/markets')).json();
          if (!this.snapMarkets.length) { this.snapMarket = 'upbit|SIM-BTC'; return; }
          const keys = this.snapMarkets.map(m => m.exchange + '|' + m.market);
          // Keep current selection if still present; otherwise default to the most recent
          // *real* listing (skip simulated SIM-* pairs), falling back to whatever exists.
          if (!keys.includes(this.snapMarket)) {
            const real = this.snapMarkets.find(m => !m.market.startsWith('SIM-'));
            const pick = real || this.snapMarkets[0];
            this.snapMarket = pick.exchange + '|' + pick.market;
          }
        },

        async loadSnapshots() {
          const [exchange, market] = this.snapMarket.split('|');
          this.snapshots = await (await fetch('/api/listings/' + encodeURIComponent(market) + '/snapshots?exchange=' + encodeURIComponent(exchange))).json();
          this.renderChart();
        },
        renderChart() {
          const el = document.getElementById('snapChart'); if (!el) return;
          const labels = [...new Set(this.snapshots.map(s => '+' + s.t_offset + 's'))];
          const series = (src) => labels.map(lb => {
            const row = this.snapshots.find(s => s.source === src && ('+' + s.t_offset + 's') === lb);
            return row ? row.price : null;
          });
          const self = this;
          const css = getComputedStyle(document.documentElement);
          const c = (v) => `rgb(${css.getPropertyValue(v).trim()})`;
          const cA = (v, a) => `rgb(${css.getPropertyValue(v).trim()} / ${a})`;
          const sub = c('--sub'), grid = cA('--border', .5), primary = c('--primary'), accent = c('--accent');
          const data = { labels, datasets: [
            { label: 'Bybit', data: series('bybit'), borderColor: primary, backgroundColor: cA('--primary', .12), tension: .3, spanGaps: true, pointRadius: 4 },
            { label: 'Binance', data: series('binance'), borderColor: accent, backgroundColor: cA('--accent', .12), tension: .3, spanGaps: true, pointRadius: 4 },
          ] };
          const opts = { responsive: true, maintainAspectRatio: false,
            plugins: { legend: { labels: { color: sub } },
              tooltip: { callbacks: { label: (ctx) => ctx.dataset.label + ': ' + self.fmtNum(ctx.parsed.y) } } },
            scales: { x: { ticks: { color: sub }, grid: { color: grid } },
              y: { ticks: { color: sub, callback: (v) => self.fmtNum(v) }, grid: { color: grid } } } };
          if (_chart) { _chart.data = data; _chart.options = opts; _chart.update(); }
          else { _chart = new Chart(el, { type: 'line', data, options: opts }); }
        },

        showToast(d, kind) {
          const k = kind || 'listing';
          if(!this.prefs.toastEvents.includes(k)) return;
          if(this.prefs.toastSound){ try{ const ac=new (window.AudioContext||window.webkitAudioContext)(); const o=ac.createOscillator(); const g=ac.createGain(); o.connect(g); g.connect(ac.destination); o.frequency.value=880; g.gain.setValueAtTime(0.3,ac.currentTime); g.gain.exponentialRampToValueAtTime(0.001,ac.currentTime+0.18); o.start(); o.stop(ac.currentTime+0.18); setTimeout(()=>{try{ac.close()}catch(e){}},500); }catch(e){} }
          const t = { ...d, kind: k }; this.toast = t; setTimeout(() => { if (this.toast === t) this.toast = null; }, this.prefs.toastDuration ?? 6000); },
        scrollLogs() { this.$nextTick(() => { const el = document.getElementById('logbox'); if (el) el.scrollTop = el.scrollHeight; }); },
        fmtUptime(s) { if (s == null) return '—'; s = Math.floor(s); const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), ss = s%60; return (h ? h+'h ' : '') + String(m).padStart(2,'0')+'m '+String(ss).padStart(2,'0')+'s'; },
        fmtTime(iso){ if(!iso) return '—'; try{ const d=new Date(iso);
          if(this.prefs.timeFormat==='utc') return d.toISOString().slice(11,19)+'Z';
          if(this.prefs.timeFormat==='relative'){ const s=Math.round((Date.now()-d)/1000);
            if(s<60) return s+'s ago'; if(s<3600) return Math.round(s/60)+'m ago'; return Math.round(s/3600)+'h ago'; }
          return d.toLocaleTimeString(); }catch(e){ return iso; } },
      };
    }
