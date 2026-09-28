/* StreamRec — front-end (JS puro) consumindo a API FastAPI via /api (proxy nginx). */
const API = "/api";
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = {
  user: null,          // {id, name, age, history}
  watched: new Set(),
  ratings: new Map(),  // movie_id -> score
  movies: null,        // catálogo completo (busca)
  genres: [],
  cacheLog: [],        // histórico de chamadas de recomendação
};

// ------------------------------------------------------------------ API
async function api(path, opts = {}) {
  const t0 = performance.now();
  const res = await fetch(API + path, { headers: { "Content-Type": "application/json" }, ...opts });
  const ms = performance.now() - t0;
  let data = null;
  try { data = await res.json(); } catch { /* sem corpo */ }
  if (!res.ok) {
    const d = data?.detail;
    const msg = Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || res.statusText;
    throw Object.assign(new Error(msg), { status: res.status });
  }
  return { data, headers: res.headers, ms };
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });

// ------------------------------------------------------------------ utilidades visuais
function hash(str) { let h = 0; for (const c of String(str)) h = (h * 31 + c.charCodeAt(0)) | 0; return Math.abs(h); }
const GENRE_HUE = { "Ação": 8, "Aventura": 30, "Animação": 48, "Comédia": 55, "Crime": 220, "Documentário": 160,
  "Drama": 280, "Fantasia": 300, "Ficção Científica": 195, "Romance": 340, "Suspense": 240, "Terror": 0 };
function posterStyle(m) {
  const h = GENRE_HUE[(m.genres || [])[0]] ?? hash(m.movie_id || m.id) % 360;
  const j = hash(m.movie_id || m.id) % 40;
  return `background: radial-gradient(circle at ${20 + j}% 20%, hsl(${h + j},65%,42%) 0%, transparent 55%),
          linear-gradient(135deg, hsl(${h},55%,24%) 0%, hsl(${(h + 40) % 360},60%,10%) 100%);`;
}
const initials = (t) => String(t).replace(/^(O|A|Os|As)\s/i, "").split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
const idOf = (m) => m.movie_id || m.id;
function matchPct(m, max) { return m.score && max ? Math.max(55, Math.min(99, Math.round(60 + 39 * (m.score / max)))) : null; }

function posterHTML(m, big = false) {
  return `<div class="poster" style="${posterStyle(m)}">
    <div class="art">${esc(initials(m.title))}</div>
    <div class="brand">S</div>
    <div class="t">${esc(m.title)}</div>
  </div>`;
}

function cardHTML(m, opts = {}) {
  const id = idOf(m);
  const pct = matchPct(m, opts.maxScore);
  const why = opts.showWhy && m.reasons?.length
    ? `<span class="why">${m.reasons.includes("usuarios_similares") ? "fãs parecidos" : m.reasons.includes("genero_favorito") ? "seu gênero" : "em alta"}</span>` : "";
  return `<div class="card ${state.watched.has(id) ? "watched" : ""} ${opts.rank ? "ranked" : ""}" data-id="${esc(id)}" title="${esc(m.title)}">
    ${posterHTML(m)}
    ${opts.rank ? `<span class="top-rank">${opts.rank}</span>` : ""}
    ${why}
    <div class="info">
      <div class="l1">${pct ? `<span class="match">${pct}% relevante</span>` : ""}
        ${m.year ? `<span class="badge">${m.year}</span>` : ""}
        ${m.avg_rating ? `<span>★ ${Number(m.avg_rating).toFixed(1)}</span>` : ""}</div>
      <div class="g">${esc((m.genres || []).join(" • "))}</div>
    </div>
  </div>`;
}

function rowHTML(title, items, opts = {}) {
  const max = Math.max(...items.map((i) => i.score || 0), 0);
  return `<section class="row">
    <h2 class="row-title">${esc(title)} ${opts.sub ? `<small>${opts.sub}</small>` : ""}</h2>
    ${items.length ? `<div class="row-wrap">
      <button class="row-arrow l" aria-label="Anterior">‹</button>
      <div class="row-track">${items.map((m, i) => cardHTML(m, { ...opts, maxScore: max, rank: opts.ranked ? i + 1 : 0 })).join("")}</div>
      <button class="row-arrow r" aria-label="Próximo">›</button>
    </div>` : `<p class="row-empty">${opts.empty || "Nada por aqui ainda."}</p>`}
  </section>`;
}

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 3200);
}

function avatarColor(id) { return `hsl(${hash(id) % 360},60%,45%)`; }
function avatarHTML(u, cls = "") {
  return `<div class="avatar ${cls}" style="background:${avatarColor(u.id)}">${esc((u.name || "?")[0])}</div>`;
}

function logCache(entry) {
  state.cacheLog.unshift({ at: new Date(), ...entry });
  state.cacheLog = state.cacheLog.slice(0, 50);
}

function showCachePill(cache, ms, ttl) {
  const p = $("#cache-pill");
  p.className = `cache-pill ${cache}`;
  p.innerHTML = `<span class="dot"></span><b>CACHE ${cache}</b> · ${ms < 10 ? ms.toFixed(1) : ms.toFixed(0)} ms no servidor${ttl != null ? ` · TTL ${ttl}s` : ""}`;
}

// ------------------------------------------------------------------ perfis
const SAVED_KEY = "streamrec.profiles";
const CURRENT_KEY = "streamrec.current";
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } },
};

async function showProfiles() {
  $("#app").classList.add("hidden");
  $("#cache-pill").classList.add("hidden");
  $("#profiles").classList.remove("hidden");
  const list = $("#profile-list");
  list.innerHTML = `<div class="avatar skeleton"></div>`.repeat(4);
  let users = [];
  try {
    const saved = store.get(SAVED_KEY, []);
    const demo = (await api("/users?limit=4")).data;
    const ids = new Set();
    users = [...saved, ...demo].filter((u) => !ids.has(u.id) && ids.add(u.id)).slice(0, 8);
  } catch (e) {
    $("#profile-error").textContent = "Não foi possível acessar a API. Verifique se os containers estão no ar.";
  }
  list.innerHTML = users.map((u) => `<div class="profile" data-id="${esc(u.id)}">${avatarHTML(u)}
      <span>${esc(u.name)}</span><small>${esc(u.id)}</small></div>`).join("");
}

async function enterProfile(id) {
  try {
    const { data } = await api(`/users/${encodeURIComponent(id)}`);
    state.user = data;
    const saved = store.get(SAVED_KEY, []).filter((u) => u.id !== data.id);
    store.set(SAVED_KEY, [{ id: data.id, name: data.name }, ...saved].slice(0, 8));
    store.set(CURRENT_KEY, data.id);
    syncHistory(data);
    $("#profiles").classList.add("hidden");
    $("#app").classList.remove("hidden");
    $("#me-avatar").outerHTML = avatarHTML(data, "sm").replace('class="avatar sm"', 'class="avatar sm" id="me-avatar"');
    $("#me-name").textContent = `${data.name} (${data.id})`;
    if (location.hash !== "#/home") location.hash = "#/home"; else route();
  } catch (e) {
    $("#profile-error").textContent = e.status === 404 ? `Usuário "${id}" não encontrado.` : e.message;
    showProfiles();
  }
}

function syncHistory(u) {
  state.watched = new Set((u.history || []).map((h) => h.movie_id));
  state.ratings = new Map((u.history || []).filter((h) => h.score != null).map((h) => [h.movie_id, h.score]));
}
async function refreshUser() {
  const { data } = await api(`/users/${encodeURIComponent(state.user.id)}`);
  state.user = data; syncHistory(data);
}

// ------------------------------------------------------------------ recomendações (com cache)
async function getRecommendations(limit = 20) {
  const r = await api(`/users/${encodeURIComponent(state.user.id)}/recommendations?limit=${limit}`);
  const cache = r.headers.get("X-Cache") || r.data.cache;
  const server = Number(r.headers.get("X-Process-Time-ms") || 0);
  logCache({ type: cache, user: state.user.id, ms: r.ms, server, ttl: r.data.ttl_seconds, strategy: r.data.strategy });
  showCachePill(cache, server || r.ms, r.data.ttl_seconds);
  return r.data;
}

// ------------------------------------------------------------------ views
const view = () => $("#view");

async function viewHome() {
  view().innerHTML = `<div class="hero skeleton"></div>`;
  const [rec, top] = await Promise.all([getRecommendations(20), api("/movies/top?limit=10").then((r) => r.data)]);
  const items = rec.items;
  const personalized = rec.strategy === "personalized";
  const hero = items[0] || top[0];

  // gêneros favoritos = mais frequentes nas recomendações
  const freq = {};
  items.forEach((m) => m.genres.forEach((g) => (freq[g] = (freq[g] || 0) + 1)));
  const favGenres = Object.entries(freq).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([g]) => g);
  const genreRows = await Promise.all(favGenres.map((g) =>
    api(`/movies?genre=${encodeURIComponent(g)}&limit=200`).then((r) => [g, r.data.filter((m) => !state.watched.has(m.id))
      .sort((a, b) => (b.avg_rating || 0) - (a.avg_rating || 0)).slice(0, 20)])));

  const history = (state.user.history || []).map((h) => ({ id: h.movie_id, title: h.title, genres: [], avg_rating: null }));

  view().innerHTML = `
    ${heroHTML(hero, personalized)}
    <div class="rows">
      ${rowHTML(personalized ? `Recomendados para ${state.user.name.split(" ")[0]}` : "Populares para começar", items,
        { showWhy: true, sub: `Neo4j + Redis · cache ${rec.cache} · ${rec.strategy === "personalized" ? "personalizado" : "cold start"}` })}
      ${rowHTML("Top 10 em alta hoje", top, { ranked: true })}
      ${genreRows.map(([g, list]) => rowHTML(`Porque você curte ${g}`, list)).join("")}
      ${rowHTML("Assistidos recentemente", history, { empty: "Você ainda não assistiu nada. Escolha um título acima!" })}
    </div>`;
}

function heroHTML(m, personalized) {
  if (!m) return `<div class="hero"></div>`;
  const genres = (m.genres || []).join(" • ");
  return `<section class="hero">
    <div class="hero-bg" style="${posterStyle(m)}"><div class="hero-art">${esc(initials(m.title))}</div></div>
    <div class="hero-content">
      <span class="hero-tag"><b>S</b> ${personalized ? "Recomendado para você" : "Em alta na StreamRec"}</span>
      <h1>${esc(m.title)}</h1>
      <div class="meta">
        ${m.score ? `<span class="match">Top escolha do algoritmo</span>` : ""}
        ${m.year ? `<span>${m.year}</span>` : ""}<span class="badge">HD</span>
        ${m.avg_rating ? `<span>★ ${Number(m.avg_rating).toFixed(1)}</span>` : ""}
      </div>
      <p>${esc(synopsis(m))}</p>
      <div class="hero-actions">
        <button class="btn-play" data-watch="${esc(idOf(m))}">▶ Assistir</button>
        <button class="btn-info" data-id="${esc(idOf(m))}">ⓘ Mais informações</button>
      </div>
    </div>
  </section>`;
}

function synopsis(m) {
  const g = (m.genres || []).map((x) => x.toLowerCase());
  const kind = g.length ? g.join(" e ") : "história";
  const why = m.reasons?.includes("usuarios_similares")
    ? "Quem tem um gosto parecido com o seu avaliou muito bem este título."
    : m.reasons?.includes("genero_favorito") ? "Combina com os gêneros que você mais assiste." : "Um dos títulos mais bem avaliados da plataforma.";
  return `Uma produção de ${kind}${m.year ? ` lançada em ${m.year}` : ""}. ${why}`;
}

async function viewTop() {
  const top = (await api("/movies/top?limit=40")).data;
  view().innerHTML = `<div class="grid-page"><h2>Em alta</h2><p class="muted" style="margin-bottom:20px">
    Mais bem avaliados (nota média × popularidade) — resultado em cache no Redis por 60 s.</p>
    <div class="grid">${top.map((m, i) => cardHTML(m, { rank: i < 10 ? i + 1 : 0 })).join("")}</div></div>`;
}

async function viewGenres(selected) {
  if (!state.genres.length) state.genres = (await api("/genres")).data;
  const g = selected || state.genres[0]?.name;
  const movies = g ? (await api(`/movies?genre=${encodeURIComponent(g)}&limit=500`)).data : [];
  view().innerHTML = `<div class="grid-page"><h2>Gêneros</h2>
    <div class="chips">${state.genres.map((x) => `<button class="chip ${x.name === g ? "on" : ""}" data-genre="${esc(x.name)}">${esc(x.name)} <small>${x.movies}</small></button>`).join("")}</div>
    <div class="grid">${movies.map((m) => cardHTML(m)).join("") || '<p class="muted">Nenhum título neste gênero.</p>'}</div></div>`;
}

async function viewHistory() {
  await refreshUser();
  const h = state.user.history || [];
  view().innerHTML = `<div class="grid-page"><h2>Assistidos por ${esc(state.user.name)}</h2>
    <p class="muted" style="margin-bottom:20px">${h.length} títulos · ${state.ratings.size} avaliados (relações WATCHED e RATED no Neo4j)</p>
    <div class="grid">${h.map((x) => cardHTML({ id: x.movie_id, title: x.title, genres: x.score ? [`Sua nota: ${"★".repeat(x.score)}`] : ["Sem nota"] })).join("")
      || '<p class="muted">Nenhum título assistido ainda.</p>'}</div></div>`;
}

async function viewSearch(q) {
  if (!state.movies) state.movies = (await api("/movies?limit=500")).data;
  const t = q.toLowerCase();
  const res = state.movies.filter((m) => m.title.toLowerCase().includes(t) || m.genres.some((g) => g.toLowerCase().includes(t)));
  view().innerHTML = `<div class="grid-page"><h2>Resultados para “${esc(q)}”</h2>
    <div class="grid">${res.map((m) => cardHTML(m)).join("") || '<p class="muted">Nenhum título encontrado.</p>'}</div></div>`;
}

async function viewAdd() {
  state.genres = (await api("/genres")).data;
  view().innerHTML = `<div class="grid-page"><h2>Adicionar título ao catálogo</h2>
    <div class="panel">
      <form id="form-movie">
        <label>Título</label><input name="title" required maxlength="80" placeholder="Ex.: Interestelar" />
        <label>Ano</label><input name="year" type="number" min="1880" max="2100" placeholder="2014" />
        <label>Gêneros</label>
        <div class="chips" id="genre-chips">${state.genres.map((g) => `<button type="button" class="chip" data-pick="${esc(g.name)}">${esc(g.name)}</button>`).join("")}</div>
        <label>Novo gênero</label>
        <div class="inline"><input id="new-genre" placeholder="Ex.: Musical" maxlength="40" /><button type="button" class="btn-outline" id="btn-genre">Registrar gênero</button></div>
        <div class="actions"><button class="btn-red">Cadastrar filme</button>
          <span class="muted">Cadastrar um filme invalida o cache de recomendações de todos os usuários.</span></div>
      </form>
    </div></div>`;
}

async function viewCache() {
  const { data: s } = await api("/cache/stats");
  const pct = (s.hit_ratio * 100).toFixed(1);
  view().innerHTML = `<div class="grid-page"><h2>Painel do cache (Redis)</h2>
    <p class="muted">Cada recomendação é buscada primeiro no Redis (chave <code>rec:user:{id}:limit:{n}</code>, TTL ${s.ttl_seconds}s).
    Se não existir (MISS), é calculada no Neo4j e gravada no cache.</p>
    <div class="stats">
      <div class="stat"><div class="k">Cache HIT</div><div class="v green">${s.hits}</div></div>
      <div class="stat"><div class="k">Cache MISS</div><div class="v orange">${s.misses}</div></div>
      <div class="stat"><div class="k">Hit ratio</div><div class="v">${pct}%</div></div>
      <div class="stat"><div class="k">Consultas ao Neo4j</div><div class="v red">${s.neo4j_queries}</div></div>
      <div class="stat"><div class="k">Chaves em cache</div><div class="v">${s.cached_recommendation_keys}</div></div>
      <div class="stat"><div class="k">Invalidadas</div><div class="v">${s.invalidated_keys}</div></div>
      <div class="stat"><div class="k">Memória Redis</div><div class="v" style="font-size:22px">${esc(s.redis_used_memory)}</div></div>
    </div>
    <div class="panel" style="max-width:none;margin-bottom:22px">
      <div class="actions" style="margin-top:0">
        <button class="btn-red" data-act="test">Buscar minhas recomendações</button>
        <button class="btn-outline" data-act="bypass">Buscar sem cache (Neo4j direto)</button>
        <button class="btn-outline" data-act="inv-me">Invalidar meu cache</button>
        <button class="btn-outline" data-act="inv-all">Invalidar tudo</button>
        <button class="btn-outline" data-act="reset">Zerar métricas</button>
      </div>
    </div>
    <h3 style="margin-bottom:10px">Últimas chamadas nesta sessão</h3>
    <div class="log">${state.cacheLog.map((l) => `<div><span class="tag ${l.type}">${l.type}</span>
      ${l.at.toLocaleTimeString()} · usuário <b>${esc(l.user)}</b>${l.ms != null ? ` · ${l.ms.toFixed(1)} ms no navegador` : ""}${l.server ? ` · ${l.server} ms no servidor` : ""}${l.ttl != null ? ` · TTL ${l.ttl}s` : ""}${l.note ? ` · ${esc(l.note)}` : ""}</div>`).join("")
      || '<div class="muted">Nenhuma chamada ainda.</div>'}</div></div>`;
}

// ------------------------------------------------------------------ modal do filme
async function openMovie(id) {
  $("#modal").classList.remove("hidden");
  document.body.style.overflow = "hidden";
  $("#modal-body").innerHTML = `<div class="modal-hero skeleton"></div>`;
  const { data: m } = await api(`/movies/${encodeURIComponent(id)}`);
  const similar = m.genres[0] ? (await api(`/movies?genre=${encodeURIComponent(m.genres[0])}&limit=200`)).data
    .filter((x) => x.id !== m.id).sort((a, b) => (b.avg_rating || 0) - (a.avg_rating || 0)).slice(0, 9) : [];
  const my = state.ratings.get(m.id) || 0;
  const watched = state.watched.has(m.id);
  $("#modal-body").innerHTML = `
    <div class="modal-hero"><div class="poster" style="${posterStyle(m)}">
      <div class="art">${esc(initials(m.title))}</div><div class="brand">S</div>
      <div class="t">${esc(m.title)}</div>
      <div class="hero-actions"><button class="btn-play" data-watch="${esc(m.id)}" ${watched ? "disabled" : ""}>${watched ? "✓ Assistido" : "▶ Assistir"}</button></div>
    </div></div>
    <div class="modal-content">
      <div>
        <div class="meta">${m.avg_rating ? `<span class="match">★ ${m.avg_rating.toFixed(1)} de 5</span>` : ""}
          ${m.year ? `<span>${m.year}</span>` : ""}<span class="badge">HD</span><span class="muted">${m.ratings} avaliações</span></div>
        <p>${esc(synopsis(m))}</p>
        <div class="rate">
          <div class="rate-label">${my ? `Sua avaliação: ${my}/5 — clique para alterar` : "Avalie este título"}</div>
          <div class="stars" data-movie="${esc(m.id)}">${[1, 2, 3, 4, 5].map((n) => `<button data-score="${n}" class="${n <= my ? "on" : ""}" title="${n}">★</button>`).join("")}</div>
        </div>
      </div>
      <div class="kv">
        <div><b>Gêneros:</b> ${esc(m.genres.join(", ") || "—")}</div>
        <div><b>ID:</b> ${esc(m.id)}</div>
        <div><b>Status:</b> ${watched ? "Assistido" : "Não assistido"}</div>
      </div>
    </div>
    ${similar.length ? `<div class="similar"><h3>Títulos semelhantes</h3><div class="grid">${similar.map((x) => cardHTML(x)).join("")}</div></div>` : ""}`;
}
function closeModal() { $("#modal").classList.add("hidden"); document.body.style.overflow = ""; }

// ------------------------------------------------------------------ ações
async function watch(id) {
  const { data } = await post(`/users/${encodeURIComponent(state.user.id)}/watched`, { movie_id: id });
  logCache({ type: "INV", user: state.user.id, note: `assistiu ${id} → ${data.cache_keys_invalidated} chave(s) invalidada(s)` });
  toast(`Adicionado aos assistidos · ${data.cache_keys_invalidated} chave(s) de cache invalidada(s)`);
  await afterInteraction(id);
}

async function rate(id, score) {
  const { data } = await post(`/users/${encodeURIComponent(state.user.id)}/ratings`, { movie_id: id, score });
  logCache({ type: "INV", user: state.user.id, note: `nota ${score} para ${id} → ${data.cache_keys_invalidated} chave(s) invalidada(s)` });
  toast(`Nota ${score}/5 registrada · recomendações serão recalculadas`);
  await afterInteraction(id);
}

async function afterInteraction(id) {
  await refreshUser();
  if (!$("#modal").classList.contains("hidden")) await openMovie(id);
  if (currentRoute() === "home") await viewHome();
}

// ------------------------------------------------------------------ roteador e eventos
const currentRoute = () => (location.hash.replace("#/", "").split("?")[0] || "home");
async function route() {
  if (!state.user) return;
  const r = currentRoute();
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === r));
  $("#cache-pill").classList.remove("hidden");
  window.scrollTo(0, 0);
  try {
    if (r === "home") await viewHome();
    else if (r === "top") await viewTop();
    else if (r === "genres") await viewGenres();
    else if (r === "history") await viewHistory();
    else if (r === "add") await viewAdd();
    else if (r === "cache") await viewCache();
    else await viewHome();
  } catch (e) {
    view().innerHTML = `<div class="grid-page"><h2>Ops!</h2><p class="muted">${esc(e.message)}</p></div>`;
  }
}

document.addEventListener("click", async (ev) => {
  const t = ev.target;
  try {
    const prof = t.closest(".profile");
    if (prof) return enterProfile(prof.dataset.id);

    const arrow = t.closest(".row-arrow");
    if (arrow) {
      const track = arrow.parentElement.querySelector(".row-track");
      track.scrollBy({ left: (arrow.classList.contains("l") ? -1 : 1) * track.clientWidth * 0.85 });
      return;
    }
    const w = t.closest("[data-watch]");
    if (w) { ev.stopPropagation(); w.disabled = true; return await watch(w.dataset.watch); }

    const star = t.closest(".stars button");
    if (star) return await rate(star.parentElement.dataset.movie, Number(star.dataset.score));

    const card = t.closest(".card, .btn-info");
    if (card?.dataset.id) return await openMovie(card.dataset.id);

    if (t.id === "modal" || t.closest("#modal-close")) return closeModal();

    const chip = t.closest("[data-genre]");
    if (chip) return await viewGenres(chip.dataset.genre);

    const pick = t.closest("[data-pick]");
    if (pick) { pick.classList.toggle("on"); return; }

    if (t.id === "btn-genre") {
      const name = $("#new-genre").value.trim();
      if (!name) return;
      await post("/genres", { name });
      toast(`Gênero “${name}” registrado no Neo4j`);
      $("#genre-chips").insertAdjacentHTML("beforeend", `<button type="button" class="chip on" data-pick="${esc(name)}">${esc(name)}</button>`);
      $("#new-genre").value = "";
      return;
    }

    const act = t.closest("[data-act]")?.dataset.act;
    if (act) {
      const uid = encodeURIComponent(state.user.id);
      if (act === "test") await getRecommendations(20);
      if (act === "bypass") {
        const r = await api(`/users/${uid}/recommendations?limit=20&use_cache=false`);
        logCache({ type: "BYPASS", user: state.user.id, ms: r.ms, server: Number(r.headers.get("X-Process-Time-ms") || 0) });
      }
      if (act === "inv-me") { const { data } = await api(`/cache/users/${uid}`, { method: "DELETE" }); logCache({ type: "INV", user: state.user.id, note: `${data.keys_removed} chave(s) removida(s)` }); toast("Cache do seu perfil invalidado"); }
      if (act === "inv-all") { const { data } = await api("/cache", { method: "DELETE" }); logCache({ type: "INV", user: "todos", note: `${data.keys_removed} chave(s) removida(s)` }); toast("Todo o cache foi invalidado"); }
      if (act === "reset") { await post("/cache/stats/reset", {}); toast("Métricas zeradas"); }
      return await viewCache();
    }

    if (t.id === "switch-profile") { ev.preventDefault(); store.set(CURRENT_KEY, null); state.user = null; return showProfiles(); }
    if (t.id === "btn-new-profile") { $("#form-new-profile").classList.toggle("hidden"); $("#form-open-profile").classList.add("hidden"); }
    if (t.id === "btn-open-profile") { $("#form-open-profile").classList.toggle("hidden"); $("#form-new-profile").classList.add("hidden"); }
  } catch (e) {
    toast(`Erro: ${e.message}`);
  }
});

document.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const fd = Object.fromEntries(new FormData(f));
  try {
    if (f.id === "form-new-profile") {
      const { data } = await post("/users", { name: fd.name, age: fd.age ? Number(fd.age) : null });
      return enterProfile(data.id);
    }
    if (f.id === "form-open-profile") return enterProfile(fd.id.trim());
    if (f.id === "form-movie") {
      const genres = [...document.querySelectorAll("#genre-chips .chip.on")].map((c) => c.dataset.pick);
      const { data } = await post("/movies", { title: fd.title, year: fd.year ? Number(fd.year) : null, genres });
      state.movies = null;
      toast(`“${data.title}” cadastrado (${data.id}) · cache de recomendações invalidado`);
      f.reset(); document.querySelectorAll("#genre-chips .chip.on").forEach((c) => c.classList.remove("on"));
      return openMovie(data.id);
    }
  } catch (e) {
    const target = f.id.startsWith("form-") && f.closest("#profiles") ? $("#profile-error") : null;
    if (target) target.textContent = e.message; else toast(`Erro: ${e.message}`);
  }
});

let searchTimer;
$("#search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  searchTimer = setTimeout(() => (q.length >= 2 ? viewSearch(q) : route()), 250);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
window.addEventListener("scroll", () => $("#topbar").classList.toggle("solid", window.scrollY > 40));
window.addEventListener("hashchange", () => { $("#search").value = ""; route(); });

// ------------------------------------------------------------------ início
const current = store.get(CURRENT_KEY, null);
if (current) enterProfile(current); else showProfiles();
