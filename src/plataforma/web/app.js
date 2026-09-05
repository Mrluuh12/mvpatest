/* Interface da plataforma. Sem etapa de build: a barreira para alguém da
 * equipe abrir e corrigir precisa ser baixa.
 *
 * Regra que atravessa o arquivo: a tela não inventa número. Onde não há
 * medição, aparece travessão — nunca zero. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const api = async (rota, opcoes) => {
    const r = await fetch(rota, opcoes);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${rota}: ${r.status}`);
    return r.json();
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const hora = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");

  const ico = {
    sino: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 8-3 8h18s-3-1-3-8"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>`,
    lista: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>`,
    modulo: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>`,
    usuario: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/></svg>`,
    ajuda: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.4 2.3c-.6.3-.9.8-.9 1.4v.3"/><path d="M12 17h.01"/></svg>`,
    filtro: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/></svg>`,
    raio: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg>`,
    relogio: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`,
    onda: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h3l2-5 4 10 3-7 2 2h4"/></svg>`,
    rede: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path d="M12 7v4M12 11 6.5 17M12 11l5.5 6"/></svg>`,
    caixa: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18"/></svg>`,
    escudo: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3 20 6v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/></svg>`,
    play: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 4v16M18 4v16"/></svg>`,
    baixar: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12M7 11l5 5 5-5M4 21h16"/></svg>`,
    terminal: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/></svg>`,
    lupa2: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>`,
    lapis: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 20h4L19 9a2 2 0 0 0-3-3L5 17z"/></svg>`,
    seta: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 9 6 6 6-6"/></svg>`,
  };

  const PAPEL = {
    radio_mesh: "Rádio malha", radio_ptp: "Rádio PtP", radio_ptmp: "Rádio PtMP",
    ihm_bordo: "IHM de bordo", hub_ptx: "Hub PTX", gateway_pneu: "Gateway pneu",
    gps: "GPS", endpoint_imx: "Endpoint IMX", plc: "CLP", conversor_can: "Conversor CAN",
    sensor_peso: "Sensor de peso", roteador: "Roteador", switch: "Switch",
    camera: "Câmera", ups: "UPS", servidor: "Servidor", periferico: "Periférico",
    desconhecido: "Não reconhecido",
  };
  const FROTA = {
    CA: "Caminhões Fora de Estrada", EH: "Escavadeiras", PF: "Perfuratrizes",
    PA: "Pás Carregadeiras", TT: "Tratores de Esteira", CP: "Comboio",
    ERB: "Estações Base", ERM: "Estações Móveis", GST: "Gate Station",
    MA: "Motoniveladoras", TN: "Tanques",
  };
  /** Como cada métrica canônica se apresenta: rótulo legível, unidade e
   *  quantas casas fazem sentido. -76,6 dBm é leitura; -76,63194 é ruído. */
  const METRICA = {
    rf_snr_db: ["SNR", "dB", 1], rf_rssi_dbm: ["Sinal recebido", "dBm", 1],
    rf_ruido_dbm: ["Ruído", "dBm", 1], rf_potencia_tx_dbm: ["Potência TX", "dBm", 0],
    rf_capacidade_estimada_mbps: ["Taxa do enlace", "Mbps", 0],
    rf_clientes_associados: ["Clientes", "", 0],
    malha_peers_ativos: ["Vizinhos ativos", "", 0],
    malha_custo_link: ["Custo do enlace", "", 0],
    disp_temperatura_c: ["Temperatura", "°C", 1], disp_cpu_pct: ["CPU", "%", 0],
    disp_bateria_pct: ["Bateria", "%", 0], disp_memoria_pct: ["Memória", "%", 0],
    ativo_uptime_s: ["Ligado há", "d", 0],
    servico_disponivel: ["Sessão BC API", "", 0],
    servico_tempo_resposta_ms: ["Resposta da API", "ms", 1],
    geo_velocidade_kmh: ["Velocidade", "km/h", 0], geo_altitude_m: ["Altitude", "m", 0],
    geo_latitude: ["Latitude", "°", 5], geo_longitude: ["Longitude", "°", 5],
  };

  /** Como o número foi obtido, em português. Um SNR que é o pior entre seis
   *  vizinhos precisa dizer isso, ou é lido como medida direta. */
  const AGREGACAO = {
    pior_entre_vizinhos: "pior entre os vizinhos",
    melhor_entre_vizinhos: "melhor entre os vizinhos",
    pior_entre_radios: "pior entre os rádios",
    maior_entre_radios: "maior entre os rádios",
    soma_dos_radios: "soma dos rádios",
  };

  const ZONA_SELO = { corporativa: "neutro", ot_nivel3: "ambar", ot_nivel2: "vermelho" };

  const S = {
    eu: null, exigeLogin: false,
    ativos: [], sinais: [], achados: {}, resumo: {}, saude: {}, transicoes: [],
    sel: null, dispSel: null, filtro: "", rapido: null, aba: "ativo",
    fichas: new Map(), abertos: new Set(["FROTA"]),
    arranjo: null, origemArranjo: "", catalogo: [], editandoTela: false, leituras: [], vizinhos: [], series: {}, eventos: [], janela: {}, metricas: [], sondas: [], diagResultado: null,
    relSel: null, relJanela: "7d", relLista: null, relParams: {},
    rede: null, redeAba: "mapa", redeSel: null, redeFiltro: "tudo",
  };

  /* três situações distintas, nunca duas */
  const situacao = (d) => {
    if (d.alcancavel === null || d.alcancavel === undefined)
      return { cls: "", selo: "neutro", txt: "não sondado", curto: "não sondado" };
    if (d.alcancavel) return { cls: "ok", selo: "verde", txt: "responde", curto: "responde" };
    return d.qualidade === "incerta"
      ? { cls: "parcial", selo: "ambar", txt: "sem resposta · incerto", curto: "sem resposta" }
      : { cls: "mau", selo: "vermelho", txt: "sem resposta", curto: "sem resposta" };
  };
  const contar = (ds) => {
    const sond = ds.filter((d) => d.alcancavel !== null && d.alcancavel !== undefined);
    const viv = sond.filter((d) => d.alcancavel).length;
    return { total: ds.length, sondados: sond.length, vivos: viv,
             mudos: sond.length - viv, fora: ds.length - sond.length };
  };
  const saudeAtivo = (ficha) => {
    if (!ficha) return { cls: "nd", txt: "sem coleta" };
    const c = contar(ficha.dispositivos);
    if (!c.sondados) return { cls: "nd", txt: "sem coleta" };
    if (c.vivos === c.sondados) return { cls: "ok", txt: "Operando" };
    return c.vivos === 0 ? { cls: "mau", txt: "Sem resposta" } : { cls: "parcial", txt: "Atenção" };
  };
  const foto = (a) => (a ? `/imagens/${encodeURIComponent(a)}` : null);

  /* ============================== entrada =============================== */
  /** As permissões vêm calculadas do servidor, por zona.
   *
   *  Havia aqui uma cópia da matriz de papéis, e cópia de regra de
   *  autorização é cópia que sai de sincronia. Saiu: uma permissão nova ficou
   *  de fora e o botão nasceu desabilitado sem ninguém entender por quê. A
   *  tela não decide autorização — ela só desenha o que o servidor já decidiu,
   *  e o servidor confere de novo a cada pedido. */
  const pode = (permissao, zona = "corporativa") =>
    Boolean(S.eu?.autenticado && (S.eu.permissoes?.[zona] || []).includes(permissao));

  function telaEntrada(recusa) {
    document.body.insertAdjacentHTML("beforeend", `
      <div class="portao" id="portao"><div class="cartao-entrada">
        <div class="cab">
          <span class="emblema"><svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 3 21 20H3z"/></svg></span>
          <span><b>FROTA MINA</b><span>plataforma de observabilidade</span></span>
        </div>
        <form id="form-entrada">
          ${recusa ? `<div class="recusa">${esc(recusa)}</div>` : ""}
          <div class="campo"><label for="ent-login">Usuário</label>
            <input id="ent-login" name="login" autocomplete="username" required autofocus></div>
          <div class="campo"><label for="ent-senha">Senha</label>
            <input id="ent-senha" name="senha" type="password"
              autocomplete="current-password" required></div>
          <button class="bt cheio" type="submit" style="justify-content:center">Entrar</button>
        </form>
        <p class="obs">Cada alteração feita aqui fica registrada com o seu nome
          e o horário.</p>
      </div></div>`);
    document.getElementById("form-entrada").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const d = new FormData(ev.target);
      try {
        await api("/api/v1/sessao", {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ login: d.get("login"), senha: d.get("senha") }),
        });
        document.getElementById("portao").remove();
        await iniciar();
      } catch (e) {
        document.getElementById("portao").remove();
        telaEntrada(e.message);
      }
    });
  }

  async function sair() {
    await fetch("/api/v1/sessao", { method: "DELETE" });
    location.reload();
  }

  /* =========================== envio de imagem ========================== */
  const seletor = $("seletor");
  let aoEscolher = null;
  seletor.addEventListener("change", async () => {
    const arq = seletor.files[0];
    if (arq && aoEscolher) await aoEscolher(arq);
    seletor.value = "";
  });
  function pedirArquivo(sujeito) {
    aoEscolher = async (arq) => {
      const corpo = new FormData();
      corpo.append("sujeito", sujeito);
      corpo.append("arquivo", arq);
      try { await api("/api/v1/imagens", { method: "POST", body: corpo });
            S.fichas.clear(); await atualizar(); }
      catch (e) { avisar("Não foi possível enviar", e.message); }
    };
    seletor.click();
  }
  const fechar = () => { $("modal").innerHTML = ""; };
  const avisar = (t, p) => {
    $("modal").innerHTML = `<div class="veu" data-fechar><div class="modal">
      <h3>${esc(t)}</h3><div class="corpo"><p>${esc(p)}</p></div>
      <div class="pe"><button class="bt" data-fechar>Fechar</button></div></div></div>`;
  };
  let aoDecidir = pedirArquivo;
  const escolher = (titulo, opcoes, entao) => {
    aoDecidir = entao || pedirArquivo;
    $("modal").innerHTML = `<div class="veu" data-fechar><div class="modal">
      <h3>${esc(titulo)}</h3><div class="corpo"><div class="escolha">
      ${opcoes.map((o) => `<button data-sujeito="${esc(o.sujeito)}">
        <b>${esc(o.titulo)}</b><span>${esc(o.nota)}</span></button>`).join("")}
      </div></div><div class="pe"><button class="bt" data-fechar>Cancelar</button></div>
    </div></div>`;
  };
  $("modal").addEventListener("click", (e) => {
    if (e.target.dataset.fechar !== undefined) return fechar();
    const b = e.target.closest("[data-sujeito]");
    if (b) { const s = b.dataset.sujeito; fechar(); aoDecidir(s); }
  });

  /* ============================== barra ================================= */
  function pintarBarra() {
    const r = S.resumo;
    const semResposta = (r.sondados ?? 0) - (r.alcancaveis ?? 0);
    const achados = Object.values(S.achados).reduce(
      (t, v) => t + (Array.isArray(v) ? v.length : 0), 0);
    const item = (icone, rotulo, n, calmo) =>
      `<button type="button">${icone}<span class="rotulo">${rotulo}</span>
       <span class="conta ${calmo ? "calmo" : ""}">${n}</span></button>`;
    $("menus").innerHTML =
      item(ico.sino, "Sem resposta", semResposta, semResposta === 0) +
      item(ico.lista, "Achados", achados, achados === 0) +
      item(ico.modulo, "Módulos", Object.keys(S.saude).length, true) +
      `<button type="button">${ico.ajuda}<span class="rotulo">Ajuda</span></button>` +
      (S.eu && S.eu.autenticado
        ? `<button type="button" data-sair title="Encerrar sessão">${ico.usuario}
             <span class="rotulo usuario-menu"><b>${esc(S.eu.nome)}</b>
             <span>${esc((S.eu.concessoes[0] || {}).papel || "")} · sair</span></span></button>`
        : `<button type="button" data-entrar>${ico.usuario}
             <span class="rotulo">Entrar</span></button>`);
    $("site").textContent = `${r.ativos ?? 0} ativos · ${r.dispositivos ?? 0} dispositivos`;
  }

  /* ============================== árvore ================================ */
  const FILTROS = [
    { id: "todos", rotulo: "Todos os ativos" },
    { id: "sem_resposta", rotulo: "Ativos sem resposta" },
    { id: "atencao", rotulo: "Ativos em atenção" },
    { id: "sem_coleta", rotulo: "Sem coleta" },
    { id: "sem_funcao", rotulo: "Sem função definida" },
  ];

  function passaNoRapido(a) {
    if (!S.rapido || S.rapido === "todos") return true;
    if (S.rapido === "sem_funcao") return a.funcao_negocio === "desconhecido";
    const f = S.fichas.get(a.ativo_id);
    if (!f) return S.rapido === "sem_coleta";
    const c = contar(f.dispositivos);
    if (S.rapido === "sem_coleta") return c.sondados === 0;
    if (S.rapido === "sem_resposta") return c.sondados > 0 && c.vivos === 0;
    if (S.rapido === "atencao") return c.sondados > 0 && c.vivos > 0 && c.vivos < c.sondados;
    return true;
  }

  function pintarRapidos() {
    const semFuncao = S.ativos.filter((a) => a.funcao_negocio === "desconhecido").length;
    $("rapidos").innerHTML = `<h4>Filtros rápidos</h4>` + FILTROS.map((f) => {
      const n = f.id === "sem_funcao" && semFuncao ? `<span class="conta">${semFuncao}</span>` : "";
      const ativo = (S.rapido || "todos") === f.id;
      return `<button data-rapido="${f.id}" aria-pressed="${ativo}">
        ${ico.filtro}${esc(f.rotulo)}${n}</button>`;
    }).join("");
  }

  function pintarArvore() {
    const busca = S.filtro.toLowerCase();
    const lista = S.ativos.filter((a) =>
      (!busca || a.ativo_id.toLowerCase().includes(busca)) && passaNoRapido(a));
    const grupos = new Map();
    for (const a of lista) {
      if (!grupos.has(a.frota)) grupos.set(a.frota, []);
      grupos.get(a.frota).push(a);
    }
    // Buscar ou filtrar abre os grupos: filtro que não revela o resultado
    // deixa o operador clicando e vendo uma árvore fechada.
    const revelando = !!busca || (S.rapido && S.rapido !== "todos");
    const raizAberta = S.abertos.has("FROTA") || revelando;
    const corpo = raizAberta ? [...grupos.entries()].map(([fr, itens]) => {
      const aberto = S.abertos.has(fr) || revelando;
      const folhas = aberto ? itens.map((a) => {
        const st = saudeAtivo(S.fichas.get(a.ativo_id));
        return `<button class="folha e-${st.cls}" data-ativo="${esc(a.ativo_id)}"
          aria-current="${a.ativo_id === S.sel}">
          <span class="pt ${st.cls === "nd" ? "" : st.cls}"></span>
          <span class="col"><b>${esc(a.ativo_id)}</b>
            <span class="${st.cls}">${esc(st.txt)}</span></span>
          <span class="qtd">${a.dispositivos.length}</span></button>`;
      }).join("") : "";
      return `<button class="no n3" data-frota="${esc(fr)}">
        <span class="chevron">${aberto ? "▼" : "▶"}</span>
        ${esc(FROTA[fr] || fr)}<span class="cont">${itens.length}</span></button>${folhas}`;
    }).join("") : "";

    $("arvore").innerHTML = grupos.size ? `
      <button class="no n1" data-raiz="SITE"><span class="chevron">▼</span>Minas</button>
      <button class="no n2" data-raiz="FROTA">
        <span class="chevron">${raizAberta ? "▼" : "▶"}</span>Frota
        <span class="cont">${lista.length}</span></button>
      ${corpo}` : `<p class="nada">nada encontrado</p>`;
  }

  /* ============================ visão geral ============================= */
  const trilha = (a) => `<nav class="trilha">
    <button data-raiz="SITE">Minas</button><span class="sep">›</span>
    <button data-raiz="FROTA">Frota</button><span class="sep">›</span>
    <button data-frota="${esc(a.frota)}">${esc(FROTA[a.frota] || a.frota)}</button>
    <span class="sep">›</span><span class="atual">${esc(a.ativo_id)}</span></nav>`;

  function topoAtivo(a, ds) {
    const st = saudeAtivo({ dispositivos: ds });
    const selo = { ok: "verde", parcial: "ambar", mau: "vermelho", nd: "neutro" }[st.cls];
    const img = foto(a.imagem);
    return `<div class="topo-ativo">
      ${img ? `<img class="retrato" src="${img}" alt="" data-foto-ativo>`
            : `<div class="retrato vazia" data-foto-ativo>+ imagem</div>`}
      <div class="tit">
        <h1>${esc(a.ativo_id)} <span class="selo ${selo}">${esc(st.txt)}</span></h1>
        <div class="subtit">
          <span><b>Frota:</b> ${esc(FROTA[a.frota] || a.frota)}</span>
          <span><b>Função:</b> ${esc(a.funcao_negocio)}</span>
          <span><b>Dispositivos:</b> ${ds.length}</span>
        </div>
      </div>
      <div class="botoes">
        <button class="bt" data-foto-ativo>${ico.lapis} Imagem</button>
        <button class="bt cheio" data-editar-ativo
          ${pode("editar_ativo") ? "" : 'disabled title="requer permissão de edição na zona corporativa"'}
          >Editar Ativo</button>
      </div></div>`;
  }

  const tit = (c, padrao) => esc(c && c.titulo ? c.titulo : padrao);

  const cxResumo = (c, a, ds) => `<section class="cx"><header><h2>${tit(c, "Resumo do Ativo")}</h2></header>
    <div class="conteudo"><dl class="pares">
      <dt>Código</dt><dd class="mono">${esc(a.ativo_id)}</dd>
      <dt>Frota</dt><dd>${esc(FROTA[a.frota] || a.frota)}</dd>
      <dt>Número</dt><dd class="mono">${esc(a.numero)}</dd>
      <dt>Função</dt><dd>${esc(a.funcao_negocio)}</dd>
      <dt>Dispositivos</dt><dd class="mono">${ds.length}</dd>
      <dt>Arestas</dt><dd class="mono">${ds.length} embarcado_em</dd>
      <dt>Zonas</dt><dd class="mono">${[...new Set(ds.map((d) => d.zona))].join(", ")}</dd>
    </dl></div></section>`;

  function cxComponentes(c0, ds) {
    const pecas = ds.map((d) => {
      const s = situacao(d);
      const img = foto(d.imagem);
      return `<article class="peca clicavel e-${s.cls || "nd"} z-${esc(d.zona)}"
        data-disp="${esc(d.chave)}">
        ${img ? `<img class="foto" src="${img}" alt="">`
              : `<div class="foto vazia" data-foto-disp="${esc(d.chave)}">+ imagem</div>`}
        ${img ? `<button class="trocar" data-foto-disp="${esc(d.chave)}" title="Trocar imagem">⌾</button>` : ""}
        <div class="txt"><div class="papel">${esc(PAPEL[d.papel] || d.papel)}</div>
          <div class="nome">${esc(d.nome)}</div>
          <span class="selo ${s.selo}" title="${esc(s.txt)}">${esc(s.curto)}</span>
        </div></article>`;
    }).join("");
    return `<section class="cx"><header><h2>${tit(c0, "Componentes")}</h2>
      <span class="dir">${ds.length} peças</span></header>
      <div class="conteudo rente diagrama">
        <div class="raiz"><span class="chip">${esc(S.sel)}</span></div>
        <div class="tronco"></div><div class="pecas">${pecas}</div>
      </div>
      <div class="legenda">
        <span><i style="background:var(--verde)"></i>Contorno verde: responde</span>
        <span><i style="background:var(--vermelho)"></i>Contorno vermelho: sem resposta</span>
        <span><i style="background:var(--ambar)"></i>Contorno âmbar: incerto</span>
        <span><i style="background:var(--linha-forte)"></i>Tracejado: não sondado</span>
      </div></section>`;
  }

  /** Alcance = respondendo ÷ sondados. A fórmula fica à vista: número composto
   *  sem definição visível é número em que ninguém confia. */
  function cxAlcance(c0, ds) {
    const c = contar(ds);
    const pct = c.sondados ? Math.round((100 * c.vivos) / c.sondados) : null;
    const r = 52, circ = 2 * Math.PI * r;
    const cor = pct === null ? "var(--linha-forte)"
      : pct === 100 ? "var(--verde)" : pct === 0 ? "var(--vermelho)" : "var(--ambar)";
    const rotulo = pct === null ? "Sem coleta"
      : pct === 100 ? "Operando" : pct === 0 ? "Sem resposta" : "Atenção";
    return `<section class="cx"><header><h2>${tit(c0, "Alcance")}</h2></header>
      <div class="conteudo"><div class="saude">
        <div class="anel">
          <svg width="118" height="118" viewBox="0 0 118 118" aria-hidden="true">
            <circle cx="59" cy="59" r="${r}" fill="none" stroke="var(--linha)" stroke-width="10"/>
            <circle cx="59" cy="59" r="${r}" fill="none" stroke="${cor}" stroke-width="10"
              stroke-linecap="round" stroke-dasharray="${pct === null ? 0 : (circ * pct) / 100} ${circ}"/>
          </svg>
          <div class="num" style="color:${cor}">${pct === null ? "—" : pct + "%"}</div>
        </div>
        <div class="rotulo" style="color:${cor}">${rotulo}</div>
        <div class="obs">alcance = respondendo ÷ sondados (${c.vivos} ÷ ${c.sondados})</div>
      </div>
      <div class="barras">
        <div class="l"><span class="pt ok"></span>Respondendo<b>${c.vivos}</b></div>
        <div class="l"><span class="pt mau"></span>Sem resposta<b>${c.mudos}</b></div>
        <div class="l"><span class="pt"></span>Não sondados<b>${c.fora}</b></div>
      </div></div></section>`;
  }

  /** Só o que foi de fato medido.
   *
   *  Antes este cartão reservava linha para RSSI, interface e temperatura,
   *  cada uma dizendo "aguarda o módulo tal". Numa tela isso é informação; em
   *  145 telas, repetido para sempre, é ruído que ensina a ignorar o cartão. O
   *  que a plataforma **não** coleta continua sendo dito — uma vez, na aba
   *  Cobertura, que é o lugar feito para isso e está a um clique daqui.
   */
  function cxTelemetria(c0, ds) {
    const sondados = ds.filter((d) => d.alcancavel !== null && d.alcancavel !== undefined);
    const vivos = sondados.filter((d) => d.alcancavel);
    const num = (xs) => xs.filter((v) => v !== null && v !== undefined);
    const lats = num(vivos.map((d) => d.latencia_ms));
    const perdas = num(sondados.map((d) => d.perda_pct));
    const med = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
    const visto = ds.map((d) => d.visto_em).filter(Boolean).sort().pop();
    const zonas = [...new Set(ds.map((d) => d.zona))];
    const papeis = new Set(ds.map((d) => d.papel));

    const linha = (icone, rotulo, valor, motivo) => `<div class="l">${icone}${rotulo}
      ${valor !== null && valor !== undefined
        ? `<span class="v">${esc(valor)}</span>`
        : `<span class="v nulo">${esc(motivo || "—")}</span>`}</div>`;
    return `<section class="cx"><header><h2>${tit(c0, "Medições")}</h2></header>
      <div class="conteudo"><div class="telem">
        ${linha(ico.raio, "Respondendo",
          sondados.length ? `${vivos.length} de ${sondados.length}` : null, "nada sondado")}
        ${linha(ico.relogio, "Latência média",
          lats.length ? med(lats).toFixed(2) + " ms" : null, "sem resposta")}
        ${linha(ico.relogio, "Pior latência",
          lats.length ? Math.max(...lats).toFixed(2) + " ms" : null, "sem resposta")}
        ${linha(ico.onda, "Perda média",
          perdas.length ? med(perdas).toFixed(0) + "%" : null)}
        ${linha(ico.caixa, "Composição",
          `${papeis.size} ${papeis.size === 1 ? "papel" : "papéis"}`)}
        ${linha(ico.rede, "Zonas", zonas.length > 1 ? `${zonas.length} distintas` : zonas[0])}
        ${linha(ico.relogio, "Última leitura", visto ? hora(visto) : null, "nunca sondado")}
      </div></div>
      <div class="pe"><button data-aba-ir="cobertura">O que ainda não é coletado</button></div>
    </section>`;
  }

  function cxTransicoes(c0) {
    const linhas = S.transicoes.slice(0, 6).map((t) => `<tr>
      <td class="mono">${esc(hora(t.em))}</td>
      <td class="nome">${esc(t.nome)}</td>
      <td><span class="selo ${t.para ? "verde" : "vermelho"}">
        ${t.de === null ? "primeira leitura" : t.para ? "voltou" : "caiu"}</span></td>
    </tr>`).join("");
    return `<section class="cx"><header><h2>${tit(c0, "Últimas mudanças")}</h2>
      <span class="dir">${S.transicoes.length}</span></header>
      <div class="conteudo rente"><div class="rol"><table>
        <thead><tr><th>Quando</th><th>Dispositivo</th><th>O quê</th></tr></thead>
        <tbody>${linhas || `<tr><td colspan="3" class="nulo">nenhuma mudança registrada</td></tr>`}</tbody>
      </table></div></div></section>`;
  }

  const cxAcoes = (c0) => `<section class="cx"><header><h2>${tit(c0, "Ações Rápidas")}</h2></header>
    <div class="conteudo"><div class="acoes">
      ${[[ico.play, "Reiniciar dispositivo"], [ico.baixar, "Exportar logs"],
         [ico.terminal, "Abrir terminal"], [ico.lupa2, "Executar diagnóstico"]]
        .map(([i, r]) => `<button class="bt" disabled
          title="Subsistema de ação — marco M4">${i}${r}</button>`).join("")}
    </div></div>
    <div class="pe"><button data-aba-ir="cobertura">Por que estão desativadas</button></div>
  </section>`;

  function cxColeta() {
    const linhas = Object.entries(S.saude).map(([n, s]) => `<tr>
      <td class="mono">${esc(n)}</td><td class="mono">${s.alvos_total}</td>
      <td class="mono">${s.alvos_falha}</td><td class="mono">${s.duracao_s} s</td>
      <td>${s.ultima_coleta_ok
        ? `<span class="selo verde">${esc(s.ultima_coleta_ok.slice(11, 19))}</span>`
        : `<span class="selo vermelho">nunca</span>`}</td></tr>`).join("");
    return `<section class="cx"><header><h2>Coleta</h2></header>
      <div class="conteudo rente"><div class="rol"><table>
      <thead><tr><th>Módulo</th><th>Alvos</th><th>Falhas</th><th>Duração</th><th>Última ok</th></tr></thead>
      <tbody>${linhas || `<tr><td colspan="5" class="nulo">nenhum módulo reportou</td></tr>`}</tbody>
      </table></div></div></section>`;
  }

  function tabelaDispositivos(c0, ds) {
    const linhas = ds.map((d) => {
      const s = situacao(d);
      const img = foto(d.imagem);
      const num = (v, suf) => (v === null || v === undefined)
        ? `<td class="nulo">—</td>` : `<td class="mono">${v}${suf}</td>`;
      return `<tr class="clicavel" data-disp="${esc(d.chave)}">
        <td>${img ? `<img class="mini" src="${img}" alt="">` : ""}</td>
        <td class="nome">${esc(d.nome)}</td>
        <td>${esc(PAPEL[d.papel] || d.papel)}</td>
        <td class="mono">${esc(d.ip || "—")}</td>
        <td><span class="selo liso ${ZONA_SELO[d.zona] || "neutro"}">${esc(d.zona)}</span></td>
        <td><span class="selo liso ${d.identidade === "mac" ? "verde" : "ambar"}">${esc(d.identidade)}</span></td>
        <td><span class="selo ${s.selo}">${s.txt}</span></td>
        ${num(d.latencia_ms == null ? null : d.latencia_ms.toFixed(2), " ms")}
        ${num(d.perda_pct == null ? null : d.perda_pct.toFixed(0), "%")}
      </tr>`;
    }).join("");
    return `<section class="cx"><header><h2>${tit(c0, "Dispositivos")}</h2>
      <span class="dir">${ds.length}</span></header>
      <div class="conteudo rente"><div class="rol"><table>
      <thead><tr><th></th><th>Nome</th><th>Papel</th><th>IP</th><th>Zona</th>
        <th>Identidade</th><th>Estado</th><th>Latência</th><th>Perda</th></tr></thead>
      <tbody>${linhas}</tbody></table></div></div></section>`;
  }

  /* -------------------- cartões da ficha do dispositivo ------------------ */

  const cxResumoDisp = (c, d) => `<section class="cx">
    <header><h2>${tit(c, "Resumo do dispositivo")}</h2></header>
    <div class="conteudo"><dl class="pares">
      <dt>Nome</dt><dd class="mono">${esc(d.nome)}</dd>
      <dt>Papel</dt><dd>${esc(PAPEL[d.papel] || d.papel)}</dd>
      <dt>Endereço</dt><dd class="mono">${esc(d.ip || "—")}</dd>
      <dt>Zona</dt><dd><span class="selo liso ${ZONA_SELO[d.zona] || "neutro"}">${esc(d.zona)}</span></dd>
      <dt>Ativo</dt><dd class="mono">${esc(d.ativo_id || "—")}</dd>
      <dt>Fabricante</dt><dd>${esc(d.fabricante || "—")}</dd>
    </dl></div></section>`;

  const cxIdentidade = (c, d) => {
    if (!d) return "";
    const forte = d.identidade === "mac";
    return `<section class="cx"><header><h2>${tit(c, "Identidade")}</h2></header>
      <div class="conteudo">
        <div class="telem">
          <div class="l">${ico.escudo}Resolve por
            <span class="v"><span class="selo ${forte ? "verde" : "ambar"}">${esc(d.identidade)}</span></span></div>
          <div class="l">${ico.rede}Chave<span class="v">${esc(d.chave)}</span></div>
        </div>
        ${forte ? "" : `<p class="aviso-inline">Sem MAC, a identidade cai para o nome —
          mais frágil, porque nome se repete. 47% do cadastro está assim.</p>`}
      </div></section>`;
  };

  function cxImagens(c, x) {
    const alvo = x.dispositivo || x.ativo;
    const img = foto(alvo.imagem);
    const gatilho = x.dispositivo
      ? `data-foto-disp="${esc(x.dispositivo.chave)}"` : `data-foto-ativo`;
    return `<section class="cx"><header><h2>${tit(c, "Imagens")}</h2></header>
      <div class="conteudo">
        ${img ? `<img class="foto-grande" src="${img}" alt="">`
              : `<div class="foto-grande vazia" ${gatilho}>+ adicionar imagem</div>`}
        ${img ? `<button class="bt" style="margin-top:10px" ${gatilho}>Trocar imagem</button>` : ""}
      </div></section>`;
  }

  const cxTexto = (c, i) => {
    const conteudo = (c.opcoes && c.opcoes.conteudo) || "";
    // Em edição o próprio cartão é o campo. Um prompt() do navegador não
    // aceita quebra de linha, e o que se escreve aqui é procedimento e
    // contato de fornecedor — texto de várias linhas.
    const corpo = S.editandoTela
      ? `<textarea class="campo-texto" data-texto="${i}" rows="5"
           placeholder="Procedimento, contato do fornecedor, lembrete…"
           >${esc(conteudo)}</textarea>`
      : `<p style="margin:0;white-space:pre-wrap">${
          conteudo ? esc(conteudo)
                   : `<span class="nada" style="padding:0">sem conteúdo — use Personalizar tela para escrever</span>`
        }</p>`;
    return `<section class="cx"><header><h2>${tit(c, "Observações")}</h2></header>
      <div class="conteudo">${corpo}</div></section>`;
  };

  //: Papéis que têm rádio. Só neles faz sentido reservar espaço para RSSI —
  //: num conversor CAN, "RSSI: aguardando coletor" é uma promessa falsa.
  const COM_RF = new Set(["radio_mesh", "radio_ptp", "radio_ptmp", "gateway_pneu", "hub_ptx"]);

  function cxTelemetriaDisp(c, d) {
    const st = situacao(d);
    const linha = (icone, rotulo, valor, nota, morno) => `<div class="l">${icone}${rotulo}
      ${valor !== null && valor !== undefined
        ? `<span class="v">${esc(valor)}${nota ? ` <i class="nota">${esc(nota)}</i>` : ""}</span>`
        : `<span class="v nulo">${esc(morno || "—")}</span>`}</div>`;

    // Alcance vem do ICMP e mora no estado; o resto vem dos módulos e mora
    // nas leituras. São origens diferentes e o cartão não finge que não.
    const base = [
      linha(ico.raio, "Estado", st.txt),
      linha(ico.relogio, "Latência",
        d.latencia_ms == null ? null : d.latencia_ms.toFixed(2) + " ms", "", "sem resposta"),
      linha(ico.onda, "Perda", d.perda_pct == null ? null : d.perda_pct.toFixed(0) + "%"),
    ];

    const lidas = (S.leituras || [])
      .filter((l) => METRICA[l.metrica])
      .map((l) => {
        const [rotulo, unidade, casas] = METRICA[l.metrica];
        if (l.metrica === "servico_disponivel") {
          return linha(ico.escudo, rotulo, l.valor ? "abre" : "não abre");
        }
        const bruto = l.metrica === "ativo_uptime_s" ? l.valor / 86400 : l.valor;
        const texto = `${bruto.toFixed(casas).replace(".", ",")}${unidade ? " " + unidade : ""}`;
        return linha(ico.caixa, rotulo, texto, AGREGACAO[l.rotulos?.agregacao] || "");
      });

    const semColetor = COM_RF.has(d.papel) && !lidas.length
      ? (S.sinais.find((x) => x.familia === "rf") || {}).motivo : null;

    return `<section class="cx"><header><h2>${tit(c, "Medições")}</h2>
      ${lidas.length ? `<span class="dir">${lidas.length} leituras</span>` : ""}</header>
      <div class="conteudo"><div class="telem">
        ${base.join("")}
        ${lidas.join("")}
        ${semColetor ? linha(ico.onda, "RSSI", null, "", semColetor) : ""}
        ${linha(ico.relogio, "Última leitura", d.visto_em ? hora(d.visto_em) : null,
          "", "nunca sondado")}
      </div></div></section>`;
  }

  //: Relações de rede. `embarcado_em` e `alimentacao` também são arestas
  //: deste equipamento, mas dizem onde ele mora e de onde vem a energia — não
  //: com quem ele fala. Repetir o ativo aqui, que já está no resumo, só
  //: dilui o cartão. Quem quiser vê-las põe `tipos` nas opções.
  const ENLACES_DE_REDE = ["peer_mesh", "peer_ptp", "associacao_ptmp",
                           "enlace_fisico", "dependencia_l3"];

  /* ============================== gráfico =============================== */

  /** Desenho: 720x220 no viewBox, escalado pela largura do cartão. Uma série
   *  só, então sem legenda — o título já a nomeia; e cor única, porque a
   *  identidade não está em disputa com ninguém. */
  const G = { L: 52, R: 14, T: 14, B: 26, W: 720, H: 220 };
  const areaX = G.W - G.L - G.R;
  const areaY = G.H - G.T - G.B;

  const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("pt-BR",
    { hour: "2-digit", minute: "2-digit" });
  const diaHora = (ts) => new Date(ts * 1000).toLocaleString("pt-BR",
    { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

  /** Números redondos para a escala. Um eixo que vai de 13,47 a 25,31 é um
   *  eixo que ninguém lê de relance. */
  function escala(min, max) {
    if (min === max) { min -= 1; max += 1; }
    const bruto = (max - min) / 4;
    const grandeza = Math.pow(10, Math.floor(Math.log10(bruto)));
    const passo = [1, 2, 2.5, 5, 10].find((m) => m * grandeza >= bruto) * grandeza;
    return { lo: Math.floor(min / passo) * passo, hi: Math.ceil(max / passo) * passo, passo };
  }

  const fmt = (v, casas = 1) =>
    (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("pt-BR") : v.toFixed(casas));

  /** Prefixos SI para o eixo. Um switch de 10 Gb/s escreve "90.000.000" no
   *  eixo, e ninguém lê nove dígitos de relance: lê "90 MB/s". A escolha do
   *  prefixo é feita uma vez para o gráfico inteiro, pelo maior valor — trocar
   *  de prefixo entre uma linha da grade e a seguinte faria o eixo mentir
   *  sobre a proporção. */
  const SI = [[1e9, "G"], [1e6, "M"], [1e3, "k"]];
  function prefixo(maximo) {
    const achado = SI.find(([n]) => Math.abs(maximo) >= n);
    return achado ? { div: achado[0], letra: achado[1] } : { div: 1, letra: "" };
  }
  const fmtEixo = (v, pref) =>
    pref.div === 1 ? fmt(v) : `${fmt(v / pref.div, 1)}${pref.letra}`;

  function desenhoNumerico(s) {
    const pts = s.pontos;
    if (!pts.length) return `<p class="nada">janela sem nenhum ponto</p>`;
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0] || t0 + 1;
    const e = escala(Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[1])));
    const pref = prefixo(Math.max(Math.abs(e.lo), Math.abs(e.hi)));
    const rotuloEixo = `${pref.letra}${s.unidade || ""}`;
    const px = (ts) => G.L + ((ts - t0) / (t1 - t0 || 1)) * areaX;
    const py = (v) => G.T + areaY - ((v - e.lo) / (e.hi - e.lo || 1)) * areaY;

    const grade = [];
    for (let v = e.lo; v <= e.hi + 1e-9; v += e.passo) {
      grade.push(`<line class="gg" x1="${G.L}" x2="${G.W - G.R}" y1="${py(v).toFixed(1)}"
        y2="${py(v).toFixed(1)}"/>
        <text class="ge" x="${G.L - 8}" y="${(py(v) + 4).toFixed(1)}"
          >${esc(fmtEixo(v, pref))}</text>`);
    }
    const caminho = pts.map((p, i) =>
      `${i ? "L" : "M"}${px(p[0]).toFixed(1)} ${py(p[1]).toFixed(1)}`).join(" ");

    return `<svg class="graf" viewBox="0 0 ${G.W} ${G.H}" preserveAspectRatio="none"
        role="img" aria-label="${esc(s.metrica)} ao longo do tempo">
      ${grade.join("")}
      ${rotuloEixo ? `<text class="ge un" x="${G.L - 8}" y="${G.T - 6}"
        >${esc(rotuloEixo)}</text>` : ""}
      <text class="ge x" x="${G.L}" y="${G.H - 8}">${esc(hhmm(t0))}</text>
      <text class="ge x fim" x="${G.W - G.R}" y="${G.H - 8}">${esc(hhmm(t1))}</text>
      <path class="linha" d="${caminho}"/>
      <g class="mira" hidden><line y1="${G.T}" y2="${G.T + areaY}"/><circle r="4"/></g>
      <rect class="captura" x="${G.L}" y="${G.T}" width="${areaX}" height="${areaY}"/>
    </svg>
    <div class="dica" hidden></div>`;
  }

  /** Estado ao longo do tempo é faixa, não linha: os dados são intervalos com
   *  início e fim exatos, e interpolar entre eles inventaria transições.
   *
   *  O incerto é **hachurado**, não âmbar: vermelho e âmbar têm ΔE 4,6 em
   *  deuteranopia — indistinguíveis. Nas pastilhas há texto junto e a cor é
   *  reforço; num desenho ela seria o único sinal. */
  function desenhoEstados(s) {
    const fs = s.faixas;
    if (!fs.length) return `<p class="nada">janela sem observação</p>`;
    const t0 = fs[0].inicio, t1 = fs[fs.length - 1].fim || t0 + 1;
    const px = (ts) => G.L + ((ts - t0) / (t1 - t0 || 1)) * areaX;
    const alt = 46, topo = G.T + 30;

    const barras = fs.map((f) => {
      const x = px(f.inicio), l = Math.max(1, px(f.fim) - x);
      const cls = f.alcancavel ? "ok" : (f.incerta ? "incerto" : "mau");
      const rot = f.alcancavel ? "responde" : (f.incerta ? "sem resposta · incerto" : "sem resposta");
      return `<rect class="faixa f-${cls}" x="${x.toFixed(1)}" y="${topo}"
        width="${l.toFixed(1)}" height="${alt}"
        data-de="${diaHora(f.inicio)}" data-ate="${diaHora(f.fim)}" data-rot="${esc(rot)}">
        <title>${esc(rot)} — ${esc(diaHora(f.inicio))} até ${esc(diaHora(f.fim))}</title>
      </rect>`;
    }).join("");

    const total = t1 - t0;
    const vivo = fs.filter((f) => f.alcancavel).reduce((a, f) => a + (f.fim - f.inicio), 0);
    const incerto = fs.filter((f) => !f.alcancavel && f.incerta)
      .reduce((a, f) => a + (f.fim - f.inicio), 0);

    return `<svg class="graf estados" viewBox="0 0 ${G.W} ${topo + alt + 30}"
        preserveAspectRatio="none"
        role="img" aria-label="estado ao longo do tempo">
      <defs><pattern id="hachura" width="7" height="7" patternUnits="userSpaceOnUse"
        patternTransform="rotate(45)">
        <rect width="7" height="7" class="hf"/><line x1="0" y1="0" x2="0" y2="7" class="hl"/>
      </pattern></defs>
      ${barras}
      <text class="ge x" x="${G.L}" y="${topo + alt + 20}">${esc(diaHora(t0))}</text>
      <text class="ge x fim" x="${G.W - G.R}" y="${topo + alt + 20}">${esc(diaHora(t1))}</text>
    </svg>
    <div class="leg-estado">
      <span><i class="q ok"></i>respondendo ${((vivo / total) * 100).toFixed(1)}%</span>
      <span><i class="q incerto"></i>incerto ${((incerto / total) * 100).toFixed(1)}%</span>
      <span><i class="q mau"></i>sem resposta</span>
    </div>`;
  }

  const janelaDe = (c, i) => S.janela[i] || c.opcoes?.janela || "6h";

  function cxGrafico(c, x, i) {
    const metrica = c.opcoes?.metrica || "rf_snr_db";
    const janela = janelaDe(c, i);
    const nome = (METRICA[metrica] || [metrica, "", 1])[0];
    const porta = c.opcoes?.porta || "";
    const s = (S.series || {})[`${metrica}|${janela}|${porta}`];

    let corpo;
    if (!s) corpo = `<p class="nada">carregando…</p>`;
    else if (s.tipo === "ausente")
      corpo = `<p class="sem-serie">${esc(s.motivo)}</p>`;
    else corpo = s.tipo === "estados" ? desenhoEstados(s) : desenhoNumerico(s);

    const nota = s && s.agregacao ? AGREGACAO[s.agregacao] : "";
    // A unidade vai no elemento porque o balão precisa da MESMA que o eixo
    // usou: uma métrica de contador vira taxa no servidor ("B" vira "B/s"), e
    // a tabela do cliente não sabe disso. Duas unidades no mesmo cartão é o
    // tipo de divergência que ninguém percebe até fazer uma conta errada.
    return `<section class="cx grafico" data-metrica="${esc(metrica)}"
        data-unidade="${esc((s && s.unidade) || "")}"
        data-janela="${esc(janela)}" data-cartao="${i}">
      <header><h2>${tit(c, nome)}${
        porta && !c.titulo ? ` <i class="nota">${esc(porta)}</i>` : ""}</h2>
        <span class="dir">
          ${nota ? `<i class="nota">${esc(nota)}</i>` : ""}
          ${["30m", "6h", "24h", "7d"].map((j) => `<button class="janela"
            data-janela-ir="${j}" aria-current="${j === janela}">${j}</button>`).join("")}
        </span></header>
      <div class="conteudo">${corpo}
        ${s && s.consulta ? `<p class="proveniencia" title="${esc(s.consulta)}">${
          esc(s.origem === "transicoes" ? "das transições registradas" : s.consulta)
        }</p>` : ""}
      </div></section>`;
  }

  //: Severidade do syslog vira selo. Emergência a erro são vermelhos porque
  //: o equipamento está dizendo que algo quebrou; aviso e atenção, âmbar.
  const SEV_SELO = {
    emergencia: "vermelho", alerta: "vermelho", critico: "vermelho", erro: "vermelho",
    aviso: "ambar", atencao: "ambar", informativo: "neutro", depuracao: "neutro",
  };

  function cxDiagnostico(c, x) {
    const d = x.dispositivo;
    //: A permissão é conferida na zona DESTE equipamento, não na corporativa:
    //: quem pode sondar o escritório não necessariamente pode sondar o nível 2.
    const zona = d?.zona || "corporativa";
    const podeSondar = pode("diagnosticar", zona);
    const r = S.diagResultado;
    const correndo = Boolean(r && r.rodando);
    const sondas = (S.sondas || []).map((s) => `<button class="bt" data-sonda="${esc(s.nome)}"
      title="${esc(s.descricao)}" ${podeSondar && !correndo ? "" : "disabled"}
      >${esc(s.rotulo)}</button>`).join("");
    const saida = r
      ? `<div class="saida ${correndo ? "rodando" : r.ok ? "ok" : "mau"}">
          <b>${esc(r.resumo)}</b>
          <pre>${esc((r.linhas || []).join("\n"))}</pre>
          ${r.duracao_s ? `<i class="nota">${r.duracao_s.toFixed(2)} s</i>` : ""}
         </div>`
      : `<p class="nada">escolha uma sonda — o resultado aparece aqui</p>`;
    return `<section class="cx"><header><h2>${tit(c, "Diagnóstico")}</h2>
      <span class="dir mono">${esc(d?.ip || "sem IP")}</span></header>
      <div class="conteudo">
        <div class="sondas">${sondas || `<span class="nada">nenhuma sonda</span>`}</div>
        ${podeSondar ? "" : `<p class="aviso-inline sem-permissao">Requer a permissão
          <b>diagnosticar</b> na zona <b>${esc(zona)}</b>.</p>`}
        ${saida}
      </div></section>`;
  }

  function cxEventos(c) {
    const evs = S.eventos || [];
    const linhas = evs.map((e) => `<tr>
      <td class="mono">${esc(hora(e.recebido_em))}</td>
      <td><span class="selo liso ${SEV_SELO[e.severidade] || "neutro"}"
        >${esc(e.severidade)}</span></td>
      <td class="mono">${esc(e.origem_ip)}</td>
      <td>${esc(e.mensagem)}${
        e.confianca !== "ip_de_origem"
          ? ` <i class="nota">${esc(e.confianca.replace(/_/g, " "))}</i>` : ""}</td>
    </tr>`).join("");
    return `<section class="cx"><header><h2>${tit(c, "O que ele contou")}</h2>
      ${evs.length ? `<span class="dir">${evs.length}</span>` : ""}</header>
      <div class="conteudo rente"><div class="rol"><table>
        <thead><tr><th>Quando</th><th>Grau</th><th>Origem</th><th>Mensagem</th></tr></thead>
        <tbody>${linhas || `<tr><td colspan="4" class="nulo">
          nada recebido — o receptor de syslog precisa estar rodando e o
          equipamento apontado para ele</td></tr>`}</tbody>
      </table></div></div>
      <div class="pe"><span class="obs-conf">A origem é o IP do remetente.
        Syslog sobre UDP não autentica nada: isto é o que alguém disse, não
        prova.</span></div></section>`;
  }

  function cxVizinhos(c) {
    const tipos = c.opcoes?.tipos || ENLACES_DE_REDE;
    const vs = (S.vizinhos || []).filter((v) => tipos.includes(v.tipo));
    // O enlace mede o que mede, e é assimétrico: o SNR daqui não é o de lá.
    // Mostrar o número do nosso lado com a direção explícita evita que alguém
    // conclua coisa errada sobre o outro extremo.
    const num = (v, casas, un) => v == null ? '<span class="nulo">—</span>'
      : `${v.toFixed(casas).replace(".", ",")}${un ? " " + un : ""}`;
    const linhas = vs.map((v) => {
      const m = v.medidas || {};
      const ruim = m.rf_snr_db != null && m.rf_snr_db < 10;
      return `<tr class="clicavel ${ruim ? "enlace-ruim" : ""}" data-disp="${esc(v.destino)}">
      <td class="nome">${esc(v.nome)}</td>
      <td class="mono num">${num(m.rf_snr_db, 1, "dB")}</td>
      <td class="mono num">${num(m.rf_rssi_dbm, 1, "dBm")}</td>
      <td class="mono num">${num(m.rf_capacidade_estimada_mbps, 0, "Mbps")}</td>
      <td class="mono">${esc(hora(v.desde))}</td>
      <td class="mono">${esc(v.atributos?.radio || "—")}</td></tr>`;
    }).join("");
    return `<section class="cx"><header><h2>${tit(c, "Vizinhança")}</h2>
      ${vs.length ? `<span class="dir">${vs.length}</span>` : ""}</header>
      <div class="conteudo rente"><div class="rol"><table>
        <thead><tr><th>Vizinho</th><th>SNR</th><th>Sinal</th><th>Taxa</th>
          <th>Desde</th><th>Rádio</th></tr></thead>
        <tbody>${linhas || `<tr><td colspan="6" class="nulo">
          nenhuma vizinhança observada — o módulo que a publica é o Rajant
        </td></tr>`}</tbody>
      </table></div></div>
      <div class="pe"><span class="obs-conf">Medido <b>deste lado</b> do enlace.
        O SNR que o vizinho mede não é o mesmo: antenas, alturas e ruído local
        diferem, e é a assimetria que diz de que lado está o problema.</span></div>
    </section>`;
  }

  function cxAuditoria(c) {
    const linhas = (S.auditoria || []).slice(0, 8).map((a) => `<tr>
      <td class="mono">${esc(hora(a.em))}</td>
      <td class="mono">${esc(a.login || "—")}</td>
      <td>${esc(a.acao)}</td></tr>`).join("");
    return `<section class="cx"><header><h2>${tit(c, "Histórico de alterações")}</h2></header>
      <div class="conteudo rente"><div class="rol"><table>
      <thead><tr><th>Quando</th><th>Quem</th><th>O quê</th></tr></thead>
      <tbody>${linhas || `<tr><td colspan="3" class="nulo">sem alterações registradas</td></tr>`}</tbody>
      </table></div></div></section>`;
  }

  /* ================== renderização dirigida pelo arranjo ================= */

  /** Cada tipo de cartão sabe se desenhar a partir do contexto. Acrescentar um
   *  tipo é acrescentar uma entrada aqui e outra no catálogo do servidor — o
   *  catálogo é fechado de propósito. */
  const CARTOES = {
    resumo: (c, x) => x.dispositivo ? cxResumoDisp(c, x.dispositivo)
                                    : cxResumo(c, x.ativo, x.dispositivos),
    alcance: (c, x) => cxAlcance(c, x.dispositivos),
    componentes: (c, x) => cxComponentes(c, x.dispositivos),
    telemetria: (c, x) => x.dispositivo ? cxTelemetriaDisp(c, x.dispositivo)
                                        : cxTelemetria(c, x.dispositivos),
    transicoes: (c) => cxTransicoes(c),
    dispositivos: (c, x) => tabelaDispositivos(c, x.dispositivos),
    identidade: (c, x) => cxIdentidade(c, x.dispositivo),
    vizinhos: (c) => cxVizinhos(c),
    grafico: (c, x, i) => cxGrafico(c, x, i),
    eventos: (c) => cxEventos(c),
    diagnostico: (c, x) => cxDiagnostico(c, x),
    imagens: (c, x) => cxImagens(c, x),
    texto: (c, x, i) => cxTexto(c, i),
    auditoria: (c) => cxAuditoria(c),
    acoes: (c) => cxAcoes(c),
  };

  const LARGURA = { 1: "g1c", 2: "g2c", 3: "g3c", 4: "g4c" };

  const JANELAS = ["30m", "6h", "24h", "7d", "30d"];

  /** O painel que aparece em modo de edição, um controle por opção declarada.
   *
   *  Dirigido pelo **tipo** da opção, não pelo tipo do cartão: acrescentar um
   *  cartão com opções passa a não exigir tocar em JavaScript nenhum — que é
   *  a mesma razão de o catálogo existir. */
  function controleDeOpcao(o, valor, i) {
    const id = `op-${i}-${o.nome}`;
    const comum = `data-opcao="${i}:${o.nome}" id="${id}"`;
    let controle;
    if (o.tipo === "metrica") {
      const opts = (S.metricas || []).map((m) => `<option value="${esc(m.nome)}"
        ${m.nome === valor ? "selected" : ""}>${esc(METRICA[m.nome]?.[0] || m.nome)}${
          m.unidade ? ` (${esc(m.unidade)})` : ""}</option>`).join("");
      controle = `<select ${comum}>${opts}</select>`;
    } else if (o.tipo === "janela") {
      controle = `<select ${comum}>${JANELAS.map((j) => `<option value="${j}"
        ${j === valor ? "selected" : ""}>${j}</option>`).join("")}</select>`;
    } else if (o.tipo === "escolha") {
      controle = `<select ${comum}>${(o.escolhas || []).map((c) => `<option value="${esc(c)}"
        ${c === valor ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
    } else if (o.tipo === "inteiro") {
      controle = `<input type="number" min="1" max="500" ${comum}
        value="${esc(valor ?? o.padrao ?? 10)}">`;
    } else {
      controle = `<textarea class="campo-texto" rows="4" ${comum}
        placeholder="${esc(o.ajuda || "")}">${esc(valor ?? "")}</textarea>`;
    }
    return `<label class="opcao" for="${id}"><span>${esc(o.rotulo)}</span>
      ${controle}${o.ajuda ? `<i class="nota">${esc(o.ajuda)}</i>` : ""}</label>`;
  }

  function painelDeOpcoes(cartao, i) {
    const def = S.catalogo.find((d) => d.tipo === cartao.tipo);
    // O texto edita-se dentro do próprio cartão; repetir aqui seria dois
    // campos para a mesma coisa.
    const ops = (def?.opcoes || []).filter((o) => o.tipo !== "texto");
    if (!ops.length) return "";
    return `<div class="opcoes">${ops.map((o) =>
      controleDeOpcao(o, cartao.opcoes?.[o.nome] ?? o.padrao, i)).join("")}</div>`;
  }

  function desenharArranjo(x) {
    const cartoes = (S.arranjo?.cartoes || []).filter((c) => c.visivel !== false);
    return `<div class="tela">${cartoes.map((c, i) => {
      const desenhar = CARTOES[c.tipo];
      const corpo = desenhar ? desenhar(c, x, i)
        : `<section class="cx"><header><h2>${esc(c.tipo)}</h2></header>
           <div class="conteudo"><p class="nada">tipo de cartão desconhecido</p></div></section>`;
      const ferramentas = S.editandoTela ? `<div class="ferramentas">
        <button data-mover="${i}:-1" title="Subir">↑</button>
        <button data-mover="${i}:1" title="Descer">↓</button>
        <button data-largura="${i}" title="Largura">⇔ ${c.largura}</button>
        <button data-renomear="${i}" title="Renomear">✎</button>
        <button data-remover="${i}" title="Remover">✕</button></div>` : "";
      return `<div class="vaga ${LARGURA[c.largura] || "g1c"}
        ${S.editandoTela ? "editando" : ""}">${ferramentas}${corpo}${
        S.editandoTela ? painelDeOpcoes(c, i) : ""}</div>`;
    }).join("")}</div>`;
  }

  function barraDeTela() {
    if (!pode("editar_painel")) return "";
    const origem = S.origemArranjo === "embutido"
      ? "arranjo embutido" : `arranjo de <b>${esc(S.origemArranjo)}</b>`;
    return `<div class="barra-tela">
      <span>Esta tela usa o ${origem}</span>
      ${S.editandoTela ? `
        <button class="bt" data-add-cartao>+ Cartão</button>
        <button class="bt" data-salvar-tela>Salvar arranjo</button>
        <button class="bt" data-cancelar-tela>Cancelar</button>`
        : `<button class="bt" data-editar-tela>${ico.lapis} Personalizar tela</button>`}
    </div>`;
  }

  function pintarAtivo(f) {
    const { ativo: a, dispositivos: ds } = f;
    $("centro").innerHTML = trilha(a) + topoAtivo(a, ds) + barraDeTela() +
      desenharArranjo({ ativo: a, dispositivos: ds, dispositivo: null });
  }

  /* ============================ outras abas ============================= */
  /* ============================= relatórios ============================= */

  const JANELAS_REL = ["24h", "7d", "30d", "90d"];

  /** Formata um valor pelo tipo que o servidor declarou para a coluna.
   *
   *  A tela não adivinha: percentual é percentual porque a coluna disse que é,
   *  e duração vira "2 h 14 min" em vez de "8040". A conta que quem lê faz de
   *  cabeça no meio de uma reunião é a que sai errada. */
  function valorDeColuna(v, col) {
    if (v === null || v === undefined || v === "") return "—";
    if (col.tipo === "percentual") return `${Number(v).toFixed(2).replace(".", ",")}%`;
    if (col.tipo === "duracao") return duracaoCurta(Number(v));
    if (col.tipo === "instante") return diaHora(new Date(v).getTime() / 1000);
    if (col.tipo === "numero" && typeof v === "number")
      return v.toLocaleString("pt-BR", { maximumFractionDigits: 2 });
    return String(v);
  }

  function duracaoCurta(s) {
    s = Math.round(s);
    if (s < 60) return `${s} s`;
    if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ${
      String(Math.floor((s % 3600) / 60)).padStart(2, "0")} min`;
    return `${Math.floor(s / 86400)} d ${
      String(Math.floor((s % 86400) / 3600)).padStart(2, "0")} h`;
  }

  const numerica = (col) =>
    ["numero", "percentual", "duracao"].includes(col.tipo);

  /** Controles dos parâmetros do relatório. Mesma ideia das opções de cartão:
   *  o servidor declara o tipo e a tela desenha, sem conhecer relatório nenhum. */
  function controleDeParametro(pr, valor) {
    const id = `rp-${pr.nome}`;
    const comum = `data-relparam="${esc(pr.nome)}" id="${id}"`;
    let controle;
    if (pr.tipo === "escolha") {
      controle = `<select ${comum}>${(pr.escolhas || []).map((c) => `<option value="${esc(c)}"
        ${String(c) === String(valor ?? "") ? "selected" : ""}>${
          esc(c === "" ? "todas" : c)}</option>`).join("")}</select>`;
    } else if (pr.tipo === "inteiro" || pr.tipo === "decimal") {
      controle = `<input type="number" ${pr.tipo === "decimal" ? 'step="0.1"' : ""}
        ${comum} value="${esc(valor ?? pr.padrao ?? "")}">`;
    } else {
      controle = `<input type="text" ${comum} value="${esc(valor ?? pr.padrao ?? "")}"
        placeholder="${esc(pr.ajuda || "")}">`;
    }
    return `<label class="param" for="${id}"><span>${esc(pr.rotulo)}</span>
      ${controle}${pr.ajuda ? `<i class="nota">${esc(pr.ajuda)}</i>` : ""}</label>`;
  }

  const paramsDaUrl = () => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(S.relParams || {})) {
      if (v !== "" && v !== null && v !== undefined) q.set(k, v);
    }
    return q;
  };

  async function pintarRelatorios() {
    if (!S.relLista) S.relLista = await api("/api/v1/relatorios").catch(() => []);
    const nome = S.relSel || (S.relLista[0] || {}).nome || "disponibilidade_frota";
    const def = S.relLista.find((x) => x.nome === nome) || { parametros: [] };
    const janela = S.relJanela || "7d";

    // Menu por categoria: quem procura relatório pensa "quanto a britagem
    // ficou parada", não "isto sai da tabela de transições".
    const porCat = new Map();
    for (const x of S.relLista) {
      if (!porCat.has(x.categoria_rotulo)) porCat.set(x.categoria_rotulo, []);
      porCat.get(x.categoria_rotulo).push(x);
    }
    const menu = [...porCat.entries()].map(([cat, itens]) => `
      <div class="grupo-rel"><h3>${esc(cat)}</h3>
        ${itens.map((x) => `<button class="item-rel ${x.nome === nome ? "cheio" : ""}"
          data-rel="${esc(x.nome)}" ${x.disponivel ? "" : "disabled"}
          title="${esc(x.disponivel ? x.descricao : x.motivo)}">
          <b>${esc(x.rotulo)}</b><span>${esc(x.disponivel ? x.descricao : x.motivo)}</span>
        </button>`).join("")}
      </div>`).join("");

    const janelas = JANELAS_REL.map((j) => `<button class="janela"
      data-rel-janela="${j}" aria-current="${j === janela}">${j}</button>`).join("");
    const params = (def.parametros || []).map(
      (pr) => controleDeParametro(pr, (S.relParams || {})[pr.nome])).join("");

    const esqueleto = (corpo, titulo) => {
      const q = paramsDaUrl();
      q.set("janela", janela);
      const base = `/api/v1/relatorios/${encodeURIComponent(nome)}?${q}`;
      $("centro").innerHTML = `<div class="relatorios">
        <aside class="menu-rel">${menu}</aside>
        <section class="cx corpo-rel">
          <header><h2>${esc(titulo)}</h2><span class="dir">${janelas}</span></header>
          ${params ? `<div class="params-rel">${params}
            <button class="bt cheio" data-rel-aplicar>Aplicar</button></div>` : ""}
          <div class="acoes-rel">
            <a class="bt" href="${base}&formato=csv" download>${ico.lista} CSV</a>
            <a class="bt" href="${base}&formato=impressao" target="_blank"
               rel="noopener">${ico.lapis} Imprimir / PDF</a>
          </div>
          <div class="conteudo rente">${corpo}</div>
        </section></div>`;
    };

    esqueleto(`<p class="nada">gerando…</p>`, def.rotulo || "Relatórios");

    const q = paramsDaUrl();
    q.set("janela", janela);
    const r = await api(`/api/v1/relatorios/${encodeURIComponent(nome)}?${q}`)
      .catch((e) => ({ erro: e.message }));

    if (r.erro) return esqueleto(`<p class="nada">${esc(r.erro)}</p>`, def.rotulo || "");

    const cab = r.colunas.map((c) => `<th class="${numerica(c) ? "num" : ""}">
      ${esc(c.rotulo)}${c.unidade ? ` <small>(${esc(c.unidade)})</small>` : ""}</th>`).join("");
    const linhas = r.linhas.map((l) => `<tr>${r.colunas.map((c) => {
      const bruto = l[c.nome];
      const txt = valorDeColuna(bruto, c);
      if (c.tipo === "selo")
        return `<td><span class="selo liso ${SELO_REL[bruto] || "neutro"}">${esc(txt)}</span></td>`;
      return `<td class="${numerica(c) ? "mono num" : ""}">${esc(txt)}</td>`;
    }).join("")}</tr>`).join("");

    const totais = Object.keys(r.totais || {}).length
      ? `<tr class="totais">${r.colunas.map((c, i) => i === 0
          ? `<td>Total</td>`
          : `<td class="${numerica(c) ? "mono num" : ""}">${
              c.nome in r.totais ? esc(valorDeColuna(r.totais[c.nome], c)) : ""}</td>`
        ).join("")}</tr>`
      : "";

    const corpo = `
      ${r.resumo ? `<p class="resumo-rel">${esc(r.resumo)}</p>` : ""}
      <div class="rol"><table>
        <thead><tr>${cab}</tr></thead>
        <tbody>${linhas || `<tr><td colspan="${r.colunas.length}" class="nulo">
          sem linhas no período — o que não é o mesmo que sem problema</td></tr>`}
          ${totais}</tbody></table></div>
      ${(r.notas || []).map((n) => `<p class="ressalva">${esc(n)}</p>`).join("")}`;
    esqueleto(corpo, r.titulo || def.rotulo);
  }

  //: Pastilhas do relatório. Gravidade de syslog e situação de queda usam a
  //: mesma paleta do resto da plataforma — vermelho é o equipamento dizendo
  //: que quebrou, âmbar é aviso, e o que a plataforma não sabe fica neutro.
  const SELO_REL = {
    emergencia: "vermelho", alerta: "vermelho", critico: "vermelho", erro: "vermelho",
    aviso: "ambar", atencao: "ambar",
    "em curso": "vermelho", incerta: "ambar", encerrada: "neutro",
    "não respondeu": "vermelho", respondeu: "verde",
    "história curta": "ambar", "ajuste fraco": "ambar", razoável: "verde",
    "aberto agora": "verde", fechado: "neutro",
  };


  /* ------------------------------- rede -------------------------------- */

  //: Faixas de RSSI iguais às do servidor. Duplicar aqui seria a mesma cópia
  //: que a matriz de permissões era; por isso a cor vem calculada na resposta
  //: e esta tabela só traduz a classe em pintura.
  const COR_ENLACE = { ok: "var(--verde)", atencao: "var(--ambar)",
                       mau: "var(--vermelho)", nd: "var(--linha-forte)" };
  const ABAS_REDE = [
    ["mapa", "Mapa"], ["enlaces", "Enlaces"], ["radios", "Rádios"],
    ["ptp", "Ponto a ponto"],
  ];
  const CLASSE_ENLACE = {
    espinha: "espinha dorsal", distribuicao: "distribuição", lavra: "frente de lavra",
  };

  const MAPA = { W: 980, H: 560, P: 26 };

  /** Filtros do mapa. Com 141 rádios e 545 enlaces, "mostrar tudo" é um
   *  emaranhado — e emaranhado não é informação. O padrão mostra tudo com o
   *  enlace bom apagado, de modo que o vermelho salte; os outros recortes
   *  existem para quando a pergunta é específica. */
  const FILTROS_MAPA = [
    ["tudo", "Tudo"],
    ["problema", "Só problemas"],
    ["infra", "Só infraestrutura"],
  ];
  const passaFiltro = (l, f) =>
    f === "problema" ? l.cor === "mau" || l.cor === "atencao"
      : f === "infra" ? l.classe !== "lavra"
      : true;

  function escalaDoMapa(d) {
    const k = Math.min((MAPA.W - 2 * MAPA.P) / (d.largura_m || 1),
                       (MAPA.H - 2 * MAPA.P) / (d.altura_m || 1));
    return {
      k,
      px: (x) => MAPA.P + x * k,
      py: (y) => MAPA.P + y * k,
    };
  }

  /** Barra de escala com um número redondo. Mapa sem escala vira diagrama, e
   *  em diagrama ninguém sabe se o vizinho ruim está a 200 m ou a 4 km. */
  function barraDeEscala(k) {
    const alvo = 150 / k;
    const passo = [50, 100, 200, 500, 1000, 2000, 5000].find((v) => v >= alvo) || 10000;
    const larg = passo * k;
    const y = MAPA.H - 16;
    return `<g class="escala">
      <line x1="${MAPA.P}" x2="${MAPA.P + larg}" y1="${y}" y2="${y}"/>
      <line x1="${MAPA.P}" x2="${MAPA.P}" y1="${y - 4}" y2="${y + 4}"/>
      <line x1="${MAPA.P + larg}" x2="${MAPA.P + larg}" y1="${y - 4}" y2="${y + 4}"/>
      <text x="${MAPA.P + larg / 2}" y="${y - 7}">${
        passo >= 1000 ? `${passo / 1000} km` : `${passo} m`}</text></g>`;
  }

  function desenhoMapa(d) {
    if (!d.nos.length) return `<p class="nada">nenhum rádio publica coordenada</p>`;
    const e = escalaDoMapa(d);
    const sel = S.redeSel;

    const filtro = S.redeFiltro || "tudo";
    const visiveis = d.enlaces.filter((l) => passaFiltro(l, filtro));
    const linhas = visiveis.map((l) => {
      const aceso = sel && (l.a === sel || l.b === sel);
      return `<line class="el q-${l.cor} ${aceso ? "aceso" : ""}"
        x1="${e.px(l.ax).toFixed(1)}" y1="${e.py(l.ay).toFixed(1)}"
        x2="${e.px(l.bx).toFixed(1)}" y2="${e.py(l.by).toFixed(1)}"
        stroke="${COR_ENLACE[l.cor]}"><title>${esc(l.nome_a)} ↔ ${esc(l.nome_b)}
        · ${esc(l.qualidade)}${l.rssi_pior_dbm !== null
          ? ` · ${l.rssi_pior_dbm} dBm` : ""}${l.distancia_m !== null
          ? ` · ${l.distancia_m} m` : ""}</title></line>`;
    }).join("");

    const nos = d.nos.map((n) => {
      const cls = n.alcancavel === null ? "nd"
        : (n.incerto ? "incerto" : (n.alcancavel ? "ok" : "mau"));
      //: Posição vencida some do desenho e vira contorno: mostrar o caminhão
      //: onde ele estava há três horas é pior que não mostrar.
      const venc = n.posicao_vencida ? " vencido" : "";
      const r = n.classe === "fixo" ? 7 : (n.classe === "semifixo" ? 6 : 4.5);
      const forma = n.classe === "movel"
        ? `<circle r="${r}"/>`
        : `<rect x="${-r}" y="${-r}" width="${2 * r}" height="${2 * r}" rx="1.5"/>`;
      return `<g class="no e-${cls}${venc} ${n.chave === sel ? "sel" : ""}"
        data-radio="${esc(n.chave)}"
        transform="translate(${e.px(n.x_m).toFixed(1)},${e.py(n.y_m).toFixed(1)})">
        <circle class="alvo" r="11"/>${forma}<title>${esc(n.nome)} · ${esc(n.frota)} · ${n.vizinhos} vizinhos${
          n.pior_rssi_dbm !== null ? ` · pior ${n.pior_rssi_dbm} dBm` : ""}</title></g>`;
    }).join("");

    // Rótulo só na infraestrutura: 141 nomes sobrepostos não se leem.
    const rotulos = d.nos.filter((n) => n.classe !== "movel").map((n) =>
      `<text class="rot" x="${(e.px(n.x_m) + 9).toFixed(1)}"
        y="${(e.py(n.y_m) + 3.5).toFixed(1)}">${esc(n.ativo || n.nome)}</text>`).join("");

    return `<svg class="mapa-rede" viewBox="0 0 ${MAPA.W} ${MAPA.H}"
        role="img" aria-label="Posição dos rádios e enlaces">
      <g class="els">${linhas}</g><g class="nos">${nos}</g>
      <g class="rots">${rotulos}</g>${barraDeEscala(e.k)}
      <text class="conta" x="${MAPA.W - MAPA.P}" y="${MAPA.H - 14}"
        >${visiveis.length} de ${d.enlaces.length} enlaces</text></svg>`;
  }

  const cxKpi = (rot, val, sub, cls) => `<div class="kpi ${cls || ""}">
    <span class="rot">${esc(rot)}</span><b>${esc(val)}</b>
    ${sub ? `<span class="sub">${esc(sub)}</span>` : ""}</div>`;

  function kpisDaRede(r) {
    const pct = (a, b) => (b ? `${((100 * a) / b).toFixed(1)}%` : "—");
    return `<div class="kpis">
      ${cxKpi("Rádios no ar", `${r.radios_online} / ${r.radios_total}`,
              pct(r.radios_online, r.radios_total))}
      ${cxKpi("Enlaces abertos", r.enlaces_abertos,
              `${r.enlaces_medidos} com medida`)}
      ${cxKpi("Sinal mediano", r.rssi_mediano_dbm !== null
              ? `${r.rssi_mediano_dbm} dBm` : "—",
              r.rssi_p10_dbm !== null ? `10% abaixo de ${r.rssi_p10_dbm}` : "")}
      ${cxKpi("Taxa mediana", r.capacidade_mediana_mbps !== null
              ? `${r.capacidade_mediana_mbps} Mbps` : "—", `SNR ${r.snr_mediano_db} dB`)}
      ${cxKpi("Vizinhos por rádio", r.vizinhos_media ?? "—",
              `${r.vizinhos_min}–${r.vizinhos_max}`)}
      ${cxKpi("Enlaces ruins", r.enlaces_ruins,
              "abaixo de −85 dBm", r.enlaces_ruins ? "alerta" : "")}
    </div>`;
  }

  function tabelaEnlaces(lista) {
    if (!lista.length) return `<p class="nada">nenhum enlace</p>`;
    const linhas = lista.map((l) => `<tr class="clicavel" data-radio="${esc(l.a)}">
      <td>${esc(l.nome_a)}</td><td>${esc(l.nome_b)}</td>
      <td><span class="selo liso">${esc(CLASSE_ENLACE[l.classe] || l.classe)}</span></td>
      <td class="mono num">${l.snr_ida_db ?? "—"}</td>
      <td class="mono num">${l.snr_volta_db ?? "—"}</td>
      <td class="mono num ${(l.assimetria_db ?? 0) >= 6 ? "aviso" : ""}"
        >${l.assimetria_db ?? "—"}</td>
      <td class="mono num">${l.rssi_pior_dbm ?? "—"}</td>
      <td class="mono num">${l.capacidade_mbps ?? "—"}</td>
      <td class="mono num">${l.distancia_m ?? "—"}</td>
      <td><span class="selo ${l.cor === "ok" ? "verde"
        : l.cor === "atencao" ? "ambar" : l.cor === "mau" ? "vermelho" : "neutro"}"
        >${esc(l.qualidade)}</span></td>
    </tr>`).join("");
    return `<div class="rol"><table>
      <thead><tr><th>De</th><th>Para</th><th>Classe</th>
        <th class="num">SNR ida</th><th class="num">SNR volta</th>
        <th class="num">Δ dB</th><th class="num">Sinal</th>
        <th class="num">Mbps</th><th class="num">Metros</th><th>Estado</th></tr></thead>
      <tbody>${linhas}</tbody></table></div>`;
  }

  function tabelaRadios(lista) {
    const linhas = lista.map((r) => `<tr class="clicavel" data-radio="${esc(r.chave)}">
      <td>${esc(r.nome)}</td><td>${esc(r.ativo)}</td>
      <td><span class="selo liso">${esc(r.classe)}</span></td>
      <td class="mono">${esc(r.ip || "—")}</td>
      <td class="mono num">${r.vizinhos}</td>
      <td class="mono num">${r.pior_rssi_dbm ?? "—"}</td>
      <td class="mono num">${r.melhor_rssi_dbm ?? "—"}</td>
      <td class="mono num">${r.ruido_dbm ?? "—"}</td>
      <td class="mono num">${r.potencia_tx_dbm ?? "—"}</td>
      <td class="mono num">${r.clientes ?? "—"}</td>
      <td class="mono num">${r.temperatura_c ?? "—"}</td>
      <td class="mono num">${r.velocidade_kmh ?? "—"}</td>
    </tr>`).join("");
    return `<div class="rol"><table>
      <thead><tr><th>Rádio</th><th>Ativo</th><th>Tipo</th><th>IP</th>
        <th class="num">Viz.</th><th class="num">Pior</th><th class="num">Melhor</th>
        <th class="num">Ruído</th><th class="num">TX</th><th class="num">Clientes</th>
        <th class="num">°C</th><th class="num">km/h</th></tr></thead>
      <tbody>${linhas}</tbody></table></div>`;
  }

  /** Ponto a ponto: os enlaces entre infraestrutura fixa.
   *
   *  É a espinha dorsal — se um destes cai, um pedaço da mina inteira perde
   *  rede, não um caminhão. Merece cartão por enlace em vez de linha de
   *  tabela, com os dois sentidos abertos. */
  function painelPtp(lista) {
    const fixos = lista.filter((l) => l.classe !== "lavra");
    if (!fixos.length) {
      return `<p class="nada">nenhum enlace entre infraestrutura fixa neste momento</p>`;
    }
    const cartoes = fixos.map((l) => {
      const lado = (rot, snr, rssi) => `<div class="sentido">
        <span class="rot">${esc(rot)}</span>
        <div class="par"><b>${snr ?? "—"}</b><span>dB SNR</span></div>
        <div class="par"><b>${rssi ?? "—"}</b><span>dBm</span></div></div>`;
      return `<article class="cx ptp" data-radio="${esc(l.a)}">
        <header><h2>${esc(l.nome_a)} ↔ ${esc(l.nome_b)}</h2>
          <span class="dir"><span class="selo ${l.cor === "ok" ? "verde"
            : l.cor === "atencao" ? "ambar" : "vermelho"}">${esc(l.qualidade)}</span>
          </span></header>
        <div class="conteudo">
          <div class="sentidos">
            ${lado(`${l.nome_a} → ${l.nome_b}`, l.snr_ida_db, l.rssi_ida_dbm)}
            ${lado(`${l.nome_b} → ${l.nome_a}`, l.snr_volta_db, l.rssi_volta_dbm)}
          </div>
          <dl class="pares">
            <dt>Classe</dt><dd>${esc(CLASSE_ENLACE[l.classe] || l.classe)}</dd>
            <dt>Distância</dt><dd>${l.distancia_m !== null
              ? `${l.distancia_m} m` : "—"}</dd>
            <dt>Taxa estimada</dt><dd>${l.capacidade_mbps ?? "—"} Mbps</dd>
            <dt>Custo</dt><dd>${l.custo ?? "—"}</dd>
            <dt>Assimetria</dt><dd class="${(l.assimetria_db ?? 0) >= 6 ? "aviso" : ""}"
              >${l.assimetria_db !== null ? `${l.assimetria_db} dB` : "—"}</dd>
            <dt>Aberto desde</dt><dd>${l.desde ? esc(diaHora(
              new Date(l.desde).getTime() / 1000)) : "—"}</dd>
          </dl>
        </div></article>`;
    }).join("");
    return `<div class="grade-ptp">${cartoes}</div>`;
  }

  function painelRadio(chave) {
    const r = (S.rede.radios || []).find((x) => x.chave === chave);
    if (!r) return "";
    const meus = (S.rede.enlaces || []).filter((l) => l.a === chave || l.b === chave);
    const linha = (rot, v, un) => `<dt>${esc(rot)}</dt><dd>${
      v === null || v === undefined ? "—" : `${v}${un ? ` ${un}` : ""}`}</dd>`;
    const viz = meus.slice(0, 12).map((l) => {
      const outro = l.a === chave ? l.nome_b : l.nome_a;
      return `<tr><td>${esc(outro)}</td>
        <td class="mono num">${l.rssi_pior_dbm ?? "—"}</td>
        <td class="mono num">${l.capacidade_mbps ?? "—"}</td>
        <td><span class="selo liso ${l.cor === "ok" ? "verde"
          : l.cor === "atencao" ? "ambar" : "vermelho"}">${esc(l.qualidade)}</span></td>
      </tr>`;
    }).join("");
    return `<aside class="cx painel-radio">
      <header><h2>${esc(r.nome)}</h2>
        <button class="bt" data-fechar-radio>Fechar</button></header>
      <div class="conteudo">
        <dl class="pares">
          ${linha("Ativo", r.ativo || "—")}${linha("Frota", r.frota)}
          ${linha("Tipo", r.classe)}${linha("Endereço", r.ip || "—")}
          ${linha("Vizinhos", r.vizinhos)}
          ${linha("Declarados pelo rádio", r.vizinhos_declarados)}
          ${linha("Pior sinal", r.pior_rssi_dbm, "dBm")}
          ${linha("Melhor sinal", r.melhor_rssi_dbm, "dBm")}
          ${linha("Ruído", r.ruido_dbm, "dBm")}
          ${linha("Potência TX", r.potencia_tx_dbm, "dBm")}
          ${linha("Clientes", r.clientes)}
          ${linha("Temperatura", r.temperatura_c, "°C")}
          ${linha("CPU", r.cpu_pct, "%")}${linha("Bateria", r.bateria_pct, "%")}
          ${linha("Velocidade", r.velocidade_kmh, "km/h")}
          ${linha("Altitude", r.altitude_m, "m")}
          ${linha("Resposta", r.resposta_ms, "ms")}
        </dl>
        <h3 class="sub-titulo">Vizinhança</h3>
        <div class="rol"><table><thead><tr><th>Vizinho</th>
          <th class="num">Sinal</th><th class="num">Mbps</th><th>Estado</th>
        </tr></thead><tbody>${viz}</tbody></table></div>
        ${meus.length > 12
          ? `<p class="nota-rede">${meus.length - 12} vizinhos além dos mostrados.</p>`
          : ""}
      </div></aside>`;
  }

  async function pintarRede() {
    if (!S.rede) {
      $("centro").innerHTML = `<div class="grade g1"><section class="cx">
        <div class="conteudo"><p class="nada">carregando a rede…</p></div>
      </section></div>`;
      const [resumo, enlaces, radios, mapa] = await Promise.all([
        api("/api/v1/rede/resumo").catch(() => null),
        api("/api/v1/rede/enlaces").catch(() => []),
        api("/api/v1/rede/radios").catch(() => []),
        api("/api/v1/rede/mapa").catch(() => ({ nos: [], enlaces: [] })),
      ]);
      S.rede = { resumo, enlaces, radios, mapa };
    }
    const { resumo, enlaces, radios, mapa } = S.rede;
    if (!resumo) {
      return ($("centro").innerHTML =
        `<p class="nada">a seção de rede precisa do banco</p>`);
    }
    const aba = S.redeAba || "mapa";
    const abas = ABAS_REDE.map(([k, r]) => `<button class="bt ${k === aba ? "cheio" : ""}"
      data-rede-aba="${k}">${esc(r)}</button>`).join("");

    let corpo;
    if (aba === "mapa") {
      const fbotoes = FILTROS_MAPA.map(([k, r]) => `<button class="bt pequeno
        ${k === (S.redeFiltro || "tudo") ? "cheio" : ""}"
        data-rede-filtro="${k}">${esc(r)}</button>`).join("");
      corpo = `<div class="mapa-caixa">
        <div class="filtros-mapa">${fbotoes}</div>
        ${desenhoMapa(mapa)}
        <div class="legenda-mapa">
          <span><i class="tr fixo"></i>infraestrutura</span>
          <span><i class="tr semifixo"></i>estação móvel</span>
          <span><i class="cr"></i>veículo</span>
          <span><i class="cr venc"></i>posição vencida</span>
          <span><i class="ln ok"></i>até −75 dBm</span>
          <span><i class="ln atencao"></i>−75 a −85</span>
          <span><i class="ln mau"></i>abaixo de −85</span>
        </div></div>`;
    } else if (aba === "enlaces") {
      corpo = tabelaEnlaces(enlaces);
    } else if (aba === "radios") {
      corpo = tabelaRadios(radios);
    } else {
      corpo = painelPtp(enlaces);
    }

    const notas = [];
    if (mapa.sem_gps) notas.push(`${mapa.sem_gps} rádios sem coordenada — fora do mapa.`);
    if (mapa.posicoes_vencidas) {
      notas.push(`${mapa.posicoes_vencidas} posições com mais de `
        + `${Math.round(mapa.vence_em_s / 60)} min — desenhadas em contorno.`);
    }
    if (resumo.enlaces_vizinho_fora_do_cadastro) {
      notas.push(`${resumo.enlaces_vizinho_fora_do_cadastro} enlaces apontam para um `
        + `vizinho que o cadastro não conhece.`);
    }
    if (resumo.enlaces_abertos - resumo.enlaces_bidirecionais > 0) {
      notas.push(`${resumo.enlaces_abertos - resumo.enlaces_bidirecionais} enlaces com `
        + `um sentido só medido.`);
    }
    if (resumo.enlaces_assimetricos) {
      notas.push(`${resumo.enlaces_assimetricos} com 6 dB ou mais de diferença entre `
        + `os sentidos.`);
    }
    if (resumo.radios_incertos) {
      notas.push(`${resumo.radios_incertos} rádios com estado incerto.`);
    }

    $("centro").innerHTML = `<div class="rede ${S.redeSel ? "com-painel" : ""}">
      <div class="col-rede">
        ${kpisDaRede(resumo)}
        <section class="cx">
          <header><h2>Malha</h2><span class="dir">${abas}</span></header>
          <div class="conteudo ${aba === "mapa" ? "" : "rente"}">${corpo}</div>
          ${notas.length ? `<div class="notas-rede">${
            notas.map((n) => `<span>${esc(n)}</span>`).join("")}</div>` : ""}
        </section>
      </div>
      ${S.redeSel ? painelRadio(S.redeSel) : ""}</div>`;
  }

  const pintarCobertura = () => {
    const linhas = S.sinais.map((s) => `<tr><td class="mono">${esc(s.familia)}</td>
      <td>${s.disponivel ? `<span class="selo verde">coletando</span>`
                         : `<span class="selo neutro">sem coletor</span>`}</td>
      <td>${esc(s.motivo || "—")}</td></tr>`).join("");
    $("centro").innerHTML = `<div class="grade g1"><section class="cx">
      <header><h2>Cobertura por família</h2></header>
      <div class="conteudo rente"><div class="rol"><table>
      <thead><tr><th>Família</th><th>Estado</th><th>Motivo</th></tr></thead>
      <tbody>${linhas}</tbody></table></div></div></section></div>`;
  };

  const pintarCadastro = () => {
    const titulos = {
      conflitos: "MACs repetidos entre ativos", homonimos: "Homônimos desambiguados",
      divergencias: "Nome discorda do endereço", papel_desconhecido: "Papel não reconhecido",
      fora_do_padrao: "Nome fora do padrão",
    };
    const cs = Object.entries(titulos).map(([k, t]) => {
      const itens = S.achados[k] || [];
      if (!itens.length) return "";
      return `<section class="cx"><header><h2>${t}</h2>
        <span class="dir">${itens.length}</span></header>
        <div class="conteudo rente"><div class="rol"><table><tbody>
        ${itens.slice(0, 15).map((i) => `<tr><td class="nome">${esc(i)}</td></tr>`).join("")}
        ${itens.length > 15 ? `<tr><td class="nulo">… mais ${itens.length - 15}</td></tr>` : ""}
        </tbody></table></div></div></section>`;
    }).join("");
    $("centro").innerHTML = cs ? `<div class="grade g2">${cs}</div>`
                              : `<p class="nada">nenhum achado</p>`;
  };

  const EDITAVEIS = [
    { campo: "funcao_negocio", rotulo: "Função de negócio" },
    { campo: "apelido", rotulo: "Apelido" },
    { campo: "criticidade", rotulo: "Criticidade" },
  ];

  function modalEdicao(a) {
    $("modal").innerHTML = `<div class="veu" data-fechar><div class="modal">
      <h3>Editar ${esc(a.ativo_id)}</h3>
      <form id="form-edicao" class="corpo">
        <div class="campo"><label for="ed-campo">Campo</label>
          <select id="ed-campo" name="campo">
            ${EDITAVEIS.map((e) => `<option value="${e.campo}">${e.rotulo}</option>`).join("")}
          </select></div>
        <div class="campo"><label for="ed-valor">Valor</label>
          <input id="ed-valor" name="valor" value="${esc(a.funcao_negocio)}" required></div>
        <p style="font-size:11.5px;color:var(--apagado);margin:0">
          O valor derivado automaticamente continua guardado; o seu passa a
          prevalecer, e a alteração fica registrada com o seu nome.</p>
        <div class="pe" style="margin:6px -18px -16px">
          <button type="button" class="bt" data-fechar>Cancelar</button>
          <button type="submit" class="bt cheio">Salvar</button></div>
      </form></div></div>`;
    document.getElementById("form-edicao").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const d = new FormData(ev.target);
      try {
        await api(`/api/v1/ativos/${encodeURIComponent(a.ativo_id)}/campo`, {
          method: "PUT", headers: { "content-type": "application/json" },
          body: JSON.stringify({ campo: d.get("campo"), valor: d.get("valor") }),
        });
        fechar(); S.fichas.clear(); await atualizar();
      } catch (e) { avisar("Não foi possível salvar", e.message); }
    });
  }

  /* =============================== fluxo ================================ */
  async function ficha(id) {
    if (!S.fichas.has(id)) S.fichas.set(id, await api(`/api/v1/ativos/${encodeURIComponent(id)}`));
    return S.fichas.get(id);
  }

  /* --------------------- arranjo: carregar e persistir ------------------ */

  async function carregarArranjo(contexto, chave, grupo) {
    // Em edição o arranjo em memória é a verdade: buscá-lo de novo a cada
    // repintura apagaria a mudança que acabou de ser feita — e cada mexida
    // repinta.
    if (S.editandoTela && S.arranjo) return;
    const q = new URLSearchParams({ contexto, chave });
    if (grupo) q.set("grupo", grupo);
    try {
      const r = await api(`/api/v1/arranjo?${q}`);
      S.arranjo = r.arranjo; S.origemArranjo = r.origem;
    } catch {
      S.arranjo = null; S.origemArranjo = "embutido";
    }
  }

  /** Onde gravar decide o alcance: esta máquina, ou toda a frota. É a mesma
   *  escolha das imagens, e pela mesma razão — arrumar 299 telas à mão não é
   *  trabalho que alguém termine. */
  function escoposDeGravacao(x) {
    if (x.dispositivo) {
      const d = x.dispositivo;
      return [
        { sujeito: `disp:${d.chave}`, titulo: "Somente este aparelho", nota: d.nome },
        { sujeito: `papel:${d.papel}`,
          titulo: `Todo aparelho com papel ${PAPEL[d.papel] || d.papel}`,
          nota: "vale para os demais do mesmo papel" },
        { sujeito: "padrao_dispositivo", titulo: "Qualquer dispositivo",
          nota: "só onde não houver arranjo mais específico" },
      ];
    }
    const a = x.ativo;
    return [
      { sujeito: `ativo:${a.ativo_id}`, titulo: `Somente ${a.ativo_id}`, nota: "esta máquina" },
      { sujeito: `frota:${a.frota}`, titulo: `Toda a frota ${FROTA[a.frota] || a.frota}`,
        nota: "vale para todos os ativos da frota" },
      { sujeito: "padrao_ativo", titulo: "Qualquer ativo",
        nota: "só onde não houver arranjo mais específico" },
    ];
  }

  /** Só busca o histórico se algum cartão for mostrá-lo: quem não pôs o cartão
   *  na tela não deve pagar a consulta. */
  async function carregarAuditoria(sujeito) {
    const quer = (S.arranjo?.cartoes || []).some((c) => c.tipo === "auditoria" && c.visivel !== false);
    if (!quer) { S.auditoria = []; return; }
    S.auditoria = await api(
      `/api/v1/auditoria?sujeito=${encodeURIComponent(sujeito)}&limite=8`).catch(() => []);
  }

  /** Busca só as séries que a tela vai desenhar. Um cartão que ninguém pôs
   *  no arranjo não custa consulta. */
  async function carregarSeries(chave) {
    const pedidos = (S.arranjo?.cartoes || [])
      .map((c, i) => [c, i])
      .filter(([c]) => c.tipo === "grafico" && c.visivel !== false)
      .map(([c, i]) => [c.opcoes?.metrica || "rf_snr_db", janelaDe(c, i),
                        c.opcoes?.porta || ""]);
    S.series = {};
    await Promise.all(pedidos.map(async ([m, j, p]) => {
      const q = new URLSearchParams({ chave, metrica: m, janela: j, porta: p });
      S.series[`${m}|${j}|${p}`] = await api(`/api/v1/serie?${q}`)
        .catch((e) => ({ tipo: "ausente", motivo: e.message }));
    }));
  }

  function contextoAtual(f) {
    const d = S.dispSel ? f.dispositivos.find((x) => x.chave === S.dispSel) : null;
    return { ativo: f.ativo, dispositivos: f.dispositivos, dispositivo: d };
  }

  async function gravarArranjo(x, escopo) {
    const contexto = x.dispositivo ? "dispositivo" : "ativo";
    await api(`/api/v1/arranjos/${encodeURIComponent(escopo)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ escopo, contexto, cartoes: S.arranjo.cartoes }),
    });
    S.editandoTela = false;
    await pintarAba();
  }

  function pintarDispositivo(f, d) {
    const x = { ativo: f.ativo, dispositivos: f.dispositivos, dispositivo: d };
    const st = situacao(d);
    const img = foto(d.imagem);
    $("centro").innerHTML = `<nav class="trilha">
        <button data-aba-ir="ativo">Ativos</button><span>/</span>
        <button data-voltar-ativo>${esc(f.ativo.ativo_id)}</button><span>/</span>
        <b>${esc(d.nome)}</b></nav>
      <div class="topo-ativo">
        ${img ? `<img class="retrato" src="${img}" alt="" data-foto-disp="${esc(d.chave)}">`
              : `<div class="retrato vazia" data-foto-disp="${esc(d.chave)}">+ imagem</div>`}
        <div class="tit">
          <h1>${esc(d.nome)} <span class="selo ${st.selo}">${esc(st.curto)}</span></h1>
          <div class="subtit">
            <span><b>Papel:</b> ${esc(PAPEL[d.papel] || d.papel)}</span>
            <span><b>Endereço:</b> ${esc(d.ip || "—")}</span>
            <span><b>Ativo:</b> ${esc(d.ativo_id || "—")}</span>
          </div>
        </div>
        <div class="botoes">
          <button class="bt" data-voltar-ativo>Voltar ao ativo</button>
          <button class="bt" data-foto-disp="${esc(d.chave)}">${ico.lapis} Imagem</button>
        </div></div>` + barraDeTela() + desenharArranjo(x);
  }

  async function pintarAba() {
    if (S.aba === "coleta") return $("centro").innerHTML = `<div class="grade g1">${cxColeta()}</div>`;
    if (S.aba === "rede") return pintarRede();
    if (S.aba === "cobertura") return pintarCobertura();
    if (S.aba === "relatorios") return pintarRelatorios();
    if (S.aba === "cadastro") return pintarCadastro();
    if (!S.sel) return;
    const f = await ficha(S.sel);
    // Resolver o dispositivo antes de buscar as mudanças: uma seleção que
    // sobrou de outro ativo pediria o histórico de um aparelho que não está
    // nesta tela.
    const d = S.dispSel ? f.dispositivos.find((x) => x.chave === S.dispSel) : null;
    if (!d) S.dispSel = null;
    const alvo = d ? `chave=${encodeURIComponent(d.chave)}`
                   : `ativo_id=${encodeURIComponent(S.sel)}`;
    S.transicoes = await api(`/api/v1/transicoes?${alvo}&limite=10`).catch(() => []);
    pintarArvore();
    if (S.aba === "dispositivos") {
      $("centro").innerHTML = trilha(f.ativo) +
        `<div class="grade g1">${tabelaDispositivos(null, f.dispositivos)}</div>`;
      return;
    }
    if (d) {
      await carregarArranjo("dispositivo", d.chave, d.papel);
      await carregarAuditoria(`disp:${d.chave}`);
      await carregarSeries(d.chave);
      [S.leituras, S.vizinhos] = await Promise.all([
        api(`/api/v1/leituras?sujeito=${encodeURIComponent(d.chave)}`).catch(() => []),
        api(`/api/v1/vizinhos?chave=${encodeURIComponent(d.chave)}`).catch(() => []),
      ]);
      S.eventos = await api(
        `/api/v1/eventos?sujeito=${encodeURIComponent(d.chave)}&limite=20`).catch(() => []);
      pintarDispositivo(f, d);
      return;
    }
    await carregarArranjo("ativo", f.ativo.ativo_id, f.ativo.frota);
    await carregarAuditoria(`ativo:${f.ativo.ativo_id}`);
    pintarAtivo(f);
  }

  const marcarAba = () => document.querySelectorAll(".abas button").forEach((b) =>
    b.setAttribute("aria-current", String(b.dataset.aba === S.aba)));

  async function atualizar() {
    const [resumo, ativos, sinais, achados] = await Promise.all([
      api("/api/v1/resumo"), api("/api/v1/ativos"), api("/api/v1/sinais"), api("/api/v1/achados"),
    ]);
    Object.assign(S, { resumo, ativos, sinais, achados, saude: resumo.modulos || {} });
    if (!S.sel && ativos.length) {
      S.sel = ativos.reduce((a, b) =>
        (b.dispositivos.length > a.dispositivos.length ? b : a), ativos[0]).ativo_id;
    }
    pintarBarra(); pintarRapidos(); pintarArvore(); await pintarAba();
    $("versao").textContent = `Plataforma ${resumo.ativos} ativos · ` +
      `${resumo.arestas_abertas} arestas abertas`;
    $("atualizado").textContent = `Atualizado ${new Date().toLocaleTimeString("pt-BR")}`;
  }

  /* ---------------------- modo de edição da tela ------------------------ */

  function modalCartoes(contexto) {
    const cabem = S.catalogo.filter((d) => d.contextos.includes(contexto));
    $("modal").innerHTML = `<div class="veu" data-fechar><div class="modal largo">
      <h3>Acrescentar cartão</h3><div class="corpo"><div class="escolha">
      ${cabem.map((d) => `<button ${d.disponivel ? `data-novo="${esc(d.tipo)}"` : "disabled"}>
        <b>${esc(d.titulo_padrao)}${d.disponivel ? "" : " — ainda não"}</b>
        <span>${esc(d.disponivel ? d.descricao : d.motivo)}</span></button>`).join("")}
      </div></div><div class="pe"><button class="bt" data-fechar>Cancelar</button></div>
    </div></div>`;
  }

  $("modal").addEventListener("click", (e) => {
    const b = e.target.closest("[data-novo]");
    if (!b) return;
    const def = S.catalogo.find((d) => d.tipo === b.dataset.novo);
    S.arranjo.cartoes = [...S.arranjo.cartoes,
      { tipo: def.tipo, titulo: null, largura: 1, visivel: true, opcoes: {} }];
    fechar(); pintarAba();
  });

  /** Mexe no arranjo em memória e repinta. Nada vai ao banco antes de Salvar —
   *  quem experimenta precisa poder desistir. */
  async function editarTela(e, x) {
    const cs = [...S.arranjo.cartoes];
    const mv = e.target.closest("[data-mover]");
    if (mv) {
      const [i, dir] = mv.dataset.mover.split(":").map(Number);
      const j = i + dir;
      if (j < 0 || j >= cs.length) return true;
      [cs[i], cs[j]] = [cs[j], cs[i]];
      S.arranjo.cartoes = cs; await pintarAba(); return true;
    }
    const lg = e.target.closest("[data-largura]");
    if (lg) {
      const i = Number(lg.dataset.largura);
      cs[i] = { ...cs[i], largura: (cs[i].largura % 4) + 1 };
      S.arranjo.cartoes = cs; await pintarAba(); return true;
    }
    const rn = e.target.closest("[data-renomear]");
    if (rn) {
      const i = Number(rn.dataset.renomear);
      const def = S.catalogo.find((d) => d.tipo === cs[i].tipo);
      const novo = prompt("Título do cartão:", cs[i].titulo || def?.titulo_padrao || "");
      if (novo === null) return true;
      cs[i] = { ...cs[i], titulo: novo.trim() || null };
      S.arranjo.cartoes = cs; await pintarAba(); return true;
    }
    const rm = e.target.closest("[data-remover]");
    if (rm) {
      const i = Number(rm.dataset.remover);
      if (cs.length === 1) { avisar("Não dá", "Uma tela precisa de ao menos um cartão."); return true; }
      cs.splice(i, 1);
      S.arranjo.cartoes = cs; await pintarAba(); return true;
    }
    if (e.target.closest("[data-add-cartao]")) {
      modalCartoes(x.dispositivo ? "dispositivo" : "ativo"); return true;
    }
    if (e.target.closest("[data-salvar-tela]")) {
      escolher("Para onde vale este arranjo?", escoposDeGravacao(x),
        (escopo) => gravarArranjo(x, escopo).catch((erro) =>
          avisar("Não salvou", erro.message)));
      return true;
    }
    if (e.target.closest("[data-cancelar-tela]")) {
      S.editandoTela = false; await pintarAba(); return true;
    }
    return false;
  }

  document.body.addEventListener("click", async (e) => {
    if (e.target.closest("[data-sair]")) return sair();
    if (e.target.closest("[data-entrar]")) return telaEntrada();
    if (e.target.closest("[data-editar-tela]")) {
      S.editandoTela = true; await pintarAba(); return;
    }
    if (S.editandoTela && S.arranjo && S.sel) {
      if (await editarTela(e, contextoAtual(await ficha(S.sel)))) return;
    }
    const br = e.target.closest("[data-rel]");
    if (br) {
      // Trocar de relatório zera os parâmetros: "limite=5" do Top N não quer
      // dizer nada no relatório de cobertura, e carregar valor de um para o
      // outro faz a tela responder pergunta que ninguém fez.
      S.relSel = br.dataset.rel; S.relParams = {};
      // A janela também é do relatório: sete dias serve para disponibilidade e
      // deixa a previsão vazia, porque a série é mais nova que a janela.
      const alvo = S.relLista.find((x) => x.nome === S.relSel);
      S.relJanela = (alvo && alvo.janela_padrao) || "7d";
      await pintarRelatorios(); return;
    }
    const brj = e.target.closest("[data-rel-janela]");
    if (brj) { S.relJanela = brj.dataset.relJanela; await pintarRelatorios(); return; }
    const ra = e.target.closest("[data-rede-aba]");
    if (ra) { S.redeAba = ra.dataset.redeAba; await pintarRede(); return; }
    const rf = e.target.closest("[data-rede-filtro]");
    if (rf) { S.redeFiltro = rf.dataset.redeFiltro; await pintarRede(); return; }
    const rr = e.target.closest("[data-radio]");
    if (rr) { S.redeSel = rr.dataset.radio; await pintarRede(); return; }
    if (e.target.closest("[data-fechar-radio]")) {
      S.redeSel = null; await pintarRede(); return;
    }
    if (e.target.closest("[data-rel-aplicar]")) {
      S.relParams = {};
      document.querySelectorAll("[data-relparam]").forEach((el) => {
        S.relParams[el.dataset.relparam] = el.value;
      });
      await pintarRelatorios(); return;
    }
    const bj = e.target.closest("[data-janela-ir]");
    if (bj && S.dispSel) {
      // A janela é escolha de quem está olhando **agora**, e por isso mora
      // fora do arranjo: escrever nele fazia a mudança ser descartada na
      // repintura seguinte, quando o arranjo é relido do servidor. O padrão
      // continua vindo da tela salva; isto só o sobrepõe nesta sessão.
      S.janela[bj.closest("[data-cartao]").dataset.cartao] = bj.dataset.janelaIr;
      await carregarSeries(S.dispSel);
      await pintarAba();
      return;
    }
    const bs = e.target.closest("[data-sonda]");
    if (bs && !bs.disabled && S.dispSel) {
      const nome = bs.dataset.sonda;
      //: Enquanto a sonda corre, o cartão fica marcado como tal — e os botões
      //: saem do ar. Duas sondas disparadas no mesmo equipamento por engano de
      //: clique é carga que ninguém pediu num rádio de campo.
      S.diagResultado = { ok: true, rodando: true, resumo: `rodando ${nome}…`, linhas: [] };
      await pintarAba();
      S.diagResultado = await api("/api/v1/diagnostico", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ sonda: nome, chave: S.dispSel, parametros: {} }),
      }).catch((erro) => ({ ok: false, resumo: erro.message, linhas: [] }));
      await pintarAba();
      return;
    }
    const dp = e.target.closest("[data-disp]");
    if (dp) { S.dispSel = dp.dataset.disp; S.editandoTela = false; S.diagResultado = null;
              S.aba = "ativo"; marcarAba(); await pintarAba(); return; }
    if (e.target.closest("[data-voltar-ativo]")) {
      S.dispSel = null; S.editandoTela = false; await pintarAba(); return;
    }
    if (e.target.closest("[data-editar-ativo]")) {
      const b = e.target.closest("[data-editar-ativo]");
      if (!b.disabled) return modalEdicao((await ficha(S.sel)).ativo);
      return;
    }
    const raiz = e.target.closest("[data-raiz]");
    if (raiz) { const k = raiz.dataset.raiz;
                S.abertos.has(k) ? S.abertos.delete(k) : S.abertos.add(k);
                pintarArvore(); return; }
    const fr = e.target.closest("[data-frota]");
    if (fr) { const k = fr.dataset.frota;
              S.abertos.has(k) ? S.abertos.delete(k) : S.abertos.add(k);
              pintarArvore(); return; }
    const at = e.target.closest("[data-ativo]");
    if (at) { S.sel = at.dataset.ativo; S.dispSel = null; S.editandoTela = false;
              if (!["dispositivos"].includes(S.aba)) S.aba = "ativo";
              marcarAba(); await pintarAba(); return; }
    const rp = e.target.closest("[data-rapido]");
    if (rp) { S.rapido = rp.dataset.rapido; pintarRapidos(); pintarArvore(); return; }
    const ab = e.target.closest("[data-aba]");
    if (ab && !ab.disabled) { S.aba = ab.dataset.aba; marcarAba(); await pintarAba(); return; }
    const ir = e.target.closest("[data-aba-ir]");
    if (ir) { S.aba = ir.dataset.abaIr; marcarAba(); await pintarAba(); return; }
    if (e.target.closest("[data-foto-ativo]")) {
      const a = (await ficha(S.sel)).ativo;
      return escolher("Imagem do ativo", [
        { sujeito: `ativo:${a.ativo_id}`, titulo: `Somente ${a.ativo_id}`, nota: "esta máquina" },
        { sujeito: `frota:${a.frota}`, titulo: `Toda a frota ${FROTA[a.frota] || a.frota}`,
          nota: "vale para todos os ativos da frota" },
      ]);
    }
    const fd = e.target.closest("[data-foto-disp]");
    if (fd) {
      const f = await ficha(S.sel);
      const d = f.dispositivos.find((x) => x.chave === fd.dataset.fotoDisp);
      if (d) escolher("Imagem do dispositivo", [
        { sujeito: `disp:${d.chave}`, titulo: "Somente este aparelho", nota: d.nome },
        { sujeito: `papel:${d.papel}`,
          titulo: `Todo aparelho com papel ${PAPEL[d.papel] || d.papel}`,
          nota: "vale para todo dispositivo com este papel" },
      ]);
    }
  });

  document.body.addEventListener("change", async (e) => {
    const ctl = e.target.closest("[data-opcao]");
    if (!ctl || !S.arranjo) return;
    const [indice, nome] = ctl.dataset.opcao.split(":");
    const c = S.arranjo.cartoes[Number(indice)];
    if (!c) return;
    const valor = ctl.type === "number" ? Number(ctl.value) : ctl.value;
    c.opcoes = { ...(c.opcoes || {}), [nome]: valor };
    if (c.tipo === "grafico" && S.dispSel) await carregarSeries(S.dispSel);
    await pintarAba();
  });

  document.body.addEventListener("input", (e) => {
    const ta = e.target.closest("[data-texto]");
    if (!ta || !S.arranjo) return;
    const c = S.arranjo.cartoes[Number(ta.dataset.texto)];
    if (c) c.opcoes = { ...(c.opcoes || {}), conteudo: ta.value };
  });

  /* A camada de leitura: um gráfico HTML é interativo por natureza, e sem
   * mira o usuário fica adivinhando o valor de um pixel. */
  document.body.addEventListener("mousemove", (e) => {
    const svg = e.target.closest("svg.graf:not(.estados)");
    const cx = e.target.closest(".cx.grafico");
    if (!svg || !cx) return;
    const chave = `${cx.dataset.metrica}|${cx.dataset.janela}`;
    const s = (S.series || {})[chave];
    if (!s || s.tipo !== "numerica" || !s.pontos.length) return;

    const cxr = svg.getBoundingClientRect();
    const uX = (e.clientX - cxr.left) / cxr.width * G.W;
    const pts = s.pontos;
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0] || t0 + 1;
    const alvoTs = t0 + ((uX - G.L) / areaX) * (t1 - t0);
    let melhor = pts[0];
    for (const p of pts) if (Math.abs(p[0] - alvoTs) < Math.abs(melhor[0] - alvoTs)) melhor = p;

    const e2 = escala(Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[1])));
    const px = G.L + ((melhor[0] - t0) / (t1 - t0 || 1)) * areaX;
    const py = G.T + areaY - ((melhor[1] - e2.lo) / (e2.hi - e2.lo || 1)) * areaY;

    const mira = svg.querySelector(".mira");
    mira.hidden = false;
    mira.querySelector("line").setAttribute("x1", px);
    mira.querySelector("line").setAttribute("x2", px);
    mira.querySelector("circle").setAttribute("cx", px);
    mira.querySelector("circle").setAttribute("cy", py);

    const dica = cx.querySelector(".dica");
    const un = cx.dataset.unidade || (METRICA[cx.dataset.metrica] || ["", ""])[1];
    const pref = prefixo(Math.abs(melhor[1]));
    dica.hidden = false;
    dica.innerHTML = `<b>${esc(fmtEixo(melhor[1], pref))}${
      un ? " " + esc(pref.letra + un) : ""}</b>
      <span>${esc(diaHora(melhor[0]))}</span>`;
    dica.style.left = `${(px / G.W) * 100}%`;
  });
  document.body.addEventListener("mouseout", (e) => {
    const cx = e.target.closest(".cx.grafico");
    if (!cx || cx.contains(e.relatedTarget)) return;
    cx.querySelectorAll(".mira").forEach((m) => { m.hidden = true; });
    cx.querySelectorAll(".dica").forEach((d) => { d.hidden = true; });
  });

  let t;
  $("filtro").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => { S.filtro = e.target.value.trim(); pintarArvore(); }, 120);
  });
  $("busca").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => { S.filtro = e.target.value.trim(); pintarArvore(); }, 120);
  });

  async function iniciar() {
    S.eu = await api("/api/v1/eu").catch(() => ({ autenticado: false }));
    const saude = await api("/api/v1/saude").catch(() => ({}));
    S.exigeLogin = !!saude.exige_login;
    if (S.exigeLogin && !S.eu.autenticado) { telaEntrada(); return; }
    [S.catalogo, S.metricas] = await Promise.all([
      api("/api/v1/catalogo").catch(() => []),
      api("/api/v1/metricas?com_serie=true").catch(() => []),
    ]);
    S.sondas = await api("/api/v1/sondas").catch(() => []);
    await atualizar();
  }

  iniciar().catch((erro) => {
    $("centro").innerHTML = `<p class="nada">Sem resposta da API: ${esc(erro.message)}</p>`;
  });
  setInterval(() => {
    if (!S.exigeLogin || (S.eu && S.eu.autenticado)) atualizar().catch(() => {});
  }, 30000);
})();
