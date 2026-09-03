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

  const PAPEL = {
    radio_mesh: "Rádio malha", radio_ptp: "Rádio PtP", radio_ptmp: "Rádio PtMP",
    ihm_bordo: "IHM de bordo", hub_ptx: "Hub PTX", gateway_pneu: "Gateway pneu",
    gps: "GPS", endpoint_imx: "Endpoint IMX", plc: "CLP", conversor_can: "Conversor CAN",
    sensor_peso: "Sensor de peso", roteador: "Roteador", switch: "Switch",
    camera: "Câmera", ups: "UPS", servidor: "Servidor", periferico: "Periférico",
    desconhecido: "Não reconhecido",
  };
  const FROTA = {
    CA: "Caminhões", EH: "Escavadeiras", PF: "Perfuratrizes", PA: "Pás carregadeiras",
    TT: "Tratores de esteira", CP: "Comboio", ERB: "Estações base", ERM: "Estações móveis",
    GST: "Gate station", MA: "Motoniveladoras", TN: "Tanques",
  };
  const CLASSE_ZONA = { corporativa: "neutro", ot_nivel3: "ambar", ot_nivel2: "vermelho" };

  const S = {
    ativos: [], sinais: [], achados: {}, resumo: {}, saude: {},
    sel: null, filtro: "", abertos: new Set(["CA"]), aba: "ativo", fichas: new Map(),
  };

  /* -------- três situações distintas, nunca duas ------------------------- */
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
    return { total: ds.length, sondados: sond.length, vivos: viv, mudos: sond.length - viv,
             fora: ds.length - sond.length };
  };
  const bolinha = (ficha) => {
    if (!ficha) return "";
    const c = contar(ficha.dispositivos);
    if (!c.sondados) return "";
    return c.vivos === c.sondados ? "ok" : c.vivos === 0 ? "mau" : "parcial";
  };

  const foto = (arquivo) => (arquivo ? `/imagens/${encodeURIComponent(arquivo)}` : null);

  /* ============================ envio de imagem ========================== */
  const seletor = $("seletor");
  let aoEscolher = null;
  seletor.addEventListener("change", async () => {
    const arq = seletor.files[0];
    if (arq && aoEscolher) await aoEscolher(arq);
    seletor.value = "";
  });

  function pedirArquivo(sujeito, depois) {
    aoEscolher = async (arq) => {
      const corpo = new FormData();
      corpo.append("sujeito", sujeito);
      corpo.append("arquivo", arq);
      try {
        await api("/api/v1/imagens", { method: "POST", body: corpo });
        S.fichas.clear();
        await atualizar();
        if (depois) depois();
      } catch (e) { alerta("Não foi possível enviar", e.message); }
    };
    seletor.click();
  }

  function fecharModal() { $("modal").innerHTML = ""; }

  function alerta(titulo, texto) {
    $("modal").innerHTML = `<div class="veu" data-fechar><div class="modal">
      <h3>${esc(titulo)}</h3><div class="corpo"><p>${esc(texto)}</p></div>
      <div class="rodape"><button class="botao" data-fechar>Fechar</button></div></div></div>`;
  }

  /** Escolha de abrangência — é o que evita subir 708 fotos. */
  function escolherAbrangencia(d) {
    $("modal").innerHTML = `<div class="veu" data-fechar><div class="modal">
      <h3>Imagem do dispositivo</h3>
      <div class="corpo"><div class="escolha">
        <button data-sujeito="disp:${esc(d.chave)}">
          <b>Somente este aparelho</b><span>${esc(d.nome)}</span></button>
        <button data-sujeito="papel:${esc(d.papel)}">
          <b>Todos os “${esc(PAPEL[d.papel] || d.papel)}”</b>
          <span>vale para todo dispositivo com este papel</span></button>
      </div></div>
      <div class="rodape"><button class="botao" data-fechar>Cancelar</button></div>
    </div></div>`;
  }

  function escolherAbrangenciaAtivo(a) {
    $("modal").innerHTML = `<div class="veu" data-fechar><div class="modal">
      <h3>Imagem do ativo</h3>
      <div class="corpo"><div class="escolha">
        <button data-sujeito="ativo:${esc(a.ativo_id)}">
          <b>Somente ${esc(a.ativo_id)}</b><span>esta máquina</span></button>
        <button data-sujeito="frota:${esc(a.frota)}">
          <b>Toda a frota “${esc(FROTA[a.frota] || a.frota)}”</b>
          <span>vale para todos os ativos da frota</span></button>
      </div></div>
      <div class="rodape"><button class="botao" data-fechar>Cancelar</button></div>
    </div></div>`;
  }

  $("modal").addEventListener("click", (e) => {
    if (e.target.dataset.fechar !== undefined) { fecharModal(); return; }
    const b = e.target.closest("[data-sujeito]");
    if (b) { const s = b.dataset.sujeito; fecharModal(); pedirArquivo(s); }
  });

  /* ============================== topo =================================== */
  function pintarTopo() {
    const r = S.resumo;
    $("indicadores").innerHTML = [
      ["ativos", r.ativos ?? 0, "azul"],
      ["dispositivos", r.dispositivos ?? 0, ""],
      ["sondados", r.sondados ?? 0, ""],
      ["respondendo", r.alcancaveis ?? 0, (r.alcancaveis ?? 0) ? "verde" : "vermelho"],
    ].map(([t, v, c]) => `<div class="ind ${c}"><b>${v}</b><span>${t}</span></div>`).join("");
    const m = Object.entries(S.saude)[0];
    $("sub").textContent = m ? `${m[0]} · ${m[1].alvos_total} alvos` : "sem coleta";
  }

  /* ============================= árvore ================================== */
  function pintarArvore() {
    const f = S.filtro.toLowerCase();
    const lista = f ? S.ativos.filter((a) => a.ativo_id.toLowerCase().includes(f)) : S.ativos;
    const grupos = new Map();
    for (const a of lista) {
      if (!grupos.has(a.frota)) grupos.set(a.frota, []);
      grupos.get(a.frota).push(a);
    }
    const alvo = $("arvore");
    if (!grupos.size) { alvo.innerHTML = `<p class="vazio">nada encontrado</p>`; return; }
    alvo.innerHTML = [...grupos.entries()].map(([fr, itens]) => {
      const aberto = S.abertos.has(fr) || !!S.filtro;
      const ul = aberto ? `<ul>${itens.map((a) => `<li>
        <button data-ativo="${esc(a.ativo_id)}" aria-current="${a.ativo_id === S.sel}">
          <span class="bolinha ${bolinha(S.fichas.get(a.ativo_id))}"></span>${esc(a.ativo_id)}
          <span class="qtd">${a.dispositivos.length}</span></button></li>`).join("")}</ul>` : "";
      return `<div class="grupo"><button data-frota="${esc(fr)}">
        <span class="seta">${aberto ? "▼" : "▶"}</span>${esc(FROTA[fr] || fr)}
        <span class="cont">${itens.length}</span></button>${ul}</div>`;
    }).join("");
  }

  /* ============================ visão geral ============================== */
  function trilha(a) {
    return `<nav class="trilha"><b>Mina</b><span class="sep">›</span>
      <b>Frota</b><span class="sep">›</span>
      <b>${esc(FROTA[a.frota] || a.frota)}</b><span class="sep">›</span>
      <b>${esc(a.ativo_id)}</b></nav>`;
  }

  function cabecalho(a, ds) {
    const c = contar(ds);
    const selo = !c.sondados ? `<span class="selo neutro">sem coleta</span>`
      : c.vivos === c.sondados ? `<span class="selo verde">operando</span>`
      : c.vivos === 0 ? `<span class="selo vermelho">sem resposta</span>`
      : `<span class="selo ambar">atenção</span>`;
    const img = foto(a.imagem);
    const retrato = img
      ? `<img class="retrato" src="${img}" alt="" data-foto-ativo>`
      : `<div class="retrato vazia" data-foto-ativo>+ imagem</div>`;
    return `<div class="cabecalho">
      ${retrato}
      <div class="titulo">
        <h1>${esc(a.ativo_id)} ${selo}</h1>
        <div class="linha-meta">
          <span><b>Frota</b> ${esc(FROTA[a.frota] || a.frota)}</span>
          <span><b>Função</b> ${esc(a.funcao_negocio)}</span>
          <span><b>Dispositivos</b> ${c.total}</span>
        </div>
      </div>
      <div class="acoes"><button class="botao" data-foto-ativo>Imagem</button></div>
    </div>`;
  }

  function cartaoResumo(a, ds) {
    const c = contar(ds);
    return `<section class="cartao"><h2>Resumo do ativo</h2><div class="corpo">
      <dl class="pares">
        <dt>Código</dt><dd>${esc(a.ativo_id)}</dd>
        <dt>Frota</dt><dd>${esc(a.frota)}</dd>
        <dt>Número</dt><dd>${esc(a.numero)}</dd>
        <dt>Função</dt><dd>${esc(a.funcao_negocio)}</dd>
        <dt>Dispositivos</dt><dd>${c.total}</dd>
        <dt>Arestas</dt><dd>${c.total} embarcado_em</dd>
      </dl></div></section>`;
  }

  /** Alcance = respondendo ÷ sondados. Fórmula à vista de propósito: número
   *  composto sem definição visível é número em que ninguém confia. */
  function cartaoAlcance(ds) {
    const c = contar(ds);
    const pct = c.sondados ? Math.round((100 * c.vivos) / c.sondados) : null;
    const raio = 42, circ = 2 * Math.PI * raio;
    const traco = pct === null ? 0 : (circ * pct) / 100;
    const cor = pct === null ? "var(--linha-forte)"
      : pct === 100 ? "var(--verde)" : pct === 0 ? "var(--vermelho)" : "var(--ambar)";
    return `<section class="cartao"><h2>Alcance</h2><div class="corpo">
      <div class="medidor">
        <div class="aro">
          <svg width="96" height="96" viewBox="0 0 96 96" aria-hidden="true">
            <circle cx="48" cy="48" r="${raio}" fill="none" stroke="var(--linha)" stroke-width="9"/>
            <circle cx="48" cy="48" r="${raio}" fill="none" stroke="${cor}" stroke-width="9"
              stroke-linecap="round" stroke-dasharray="${traco} ${circ}"/>
          </svg>
          <div class="valor" style="color:${cor}">${pct === null ? "—" : pct + "%"}</div>
        </div>
        <div class="legenda">
          <div><span class="bolinha ok"></span>Respondendo <b>${c.vivos}</b></div>
          <div><span class="bolinha mau"></span>Sem resposta <b>${c.mudos}</b></div>
          <div><span class="bolinha"></span>Não sondados <b>${c.fora}</b></div>
        </div>
      </div>
      <p class="formula">alcance = respondendo ÷ sondados (${c.vivos} ÷ ${c.sondados})</p>
    </div></section>`;
  }

  function cartaoComponentes(ds) {
    const pecas = ds.map((d) => {
      const s = situacao(d);
      const img = foto(d.imagem);
      const topo = img
        ? `<img class="foto" src="${img}" alt="">`
        : `<div class="foto vazia" data-foto-disp="${esc(d.chave)}">+ imagem</div>`;
      return `<article class="peca z-${esc(d.zona)}">
        ${topo}
        ${img ? `<button class="trocar" data-foto-disp="${esc(d.chave)}" title="Trocar imagem">⌾</button>` : ""}
        <div class="txt">
          <div class="papel">${esc(PAPEL[d.papel] || d.papel)}</div>
          <div class="nome">${esc(d.nome)}</div>
          <span class="selo ${s.selo}" title="${esc(s.txt)}">${esc(s.curto)}</span>
        </div></article>`;
    }).join("");
    return `<section class="cartao"><h2>Componentes</h2>
      <div class="corpo sem diagrama">
        <div class="centralizado"><div class="no-raiz">${esc(S.sel)}</div></div>
        <div class="trilho"></div>
        <div class="pecas">${pecas}</div>
      </div></section>`;
  }

  function tabelaDispositivos(ds) {
    const linhas = ds.map((d) => {
      const s = situacao(d);
      const img = foto(d.imagem);
      const cel = (v, suf = "") => (v === null || v === undefined)
        ? `<td class="nulo">—</td>` : `<td class="mono">${v}${suf}</td>`;
      return `<tr>
        <td>${img ? `<img class="mini" src="${img}" alt="">` : ""}</td>
        <td class="nome">${esc(d.nome)}</td>
        <td>${esc(PAPEL[d.papel] || d.papel)}</td>
        <td class="mono">${esc(d.ip || "—")}</td>
        <td><span class="selo liso ${CLASSE_ZONA[d.zona] || "neutro"}">${esc(d.zona)}</span></td>
        <td><span class="selo liso ${d.identidade === "mac" ? "verde" : "ambar"}">${esc(d.identidade)}</span></td>
        <td><span class="selo ${s.selo}">${s.txt}</span></td>
        ${cel(d.latencia_ms === null || d.latencia_ms === undefined ? null : d.latencia_ms.toFixed(2), " ms")}
        ${cel(d.perda_pct === null || d.perda_pct === undefined ? null : d.perda_pct.toFixed(0), "%")}
      </tr>`;
    }).join("");
    return `<section class="cartao"><h2>Dispositivos</h2><div class="corpo sem"><div class="rolagem">
      <table><thead><tr><th></th><th>Nome</th><th>Papel</th><th>IP</th><th>Zona</th>
        <th>Identidade</th><th>Estado</th><th>Latência</th><th>Perda</th></tr></thead>
      <tbody>${linhas}</tbody></table></div></div></section>`;
  }

  function pintarAtivo(ficha) {
    const { ativo: a, dispositivos: ds } = ficha;
    $("centro").innerHTML = trilha(a) + cabecalho(a, ds) +
      `<div class="grade">${cartaoResumo(a, ds)}${cartaoAlcance(ds)}</div>` +
      `<div class="grade larga">${cartaoComponentes(ds)}</div>` +
      `<div class="grade larga">${tabelaDispositivos(ds)}</div>`;
  }

  function pintarDispositivos(ficha) {
    $("centro").innerHTML = trilha(ficha.ativo) +
      `<div class="grade larga">${tabelaDispositivos(ficha.dispositivos)}</div>`;
  }

  /* ============================ outras abas ============================== */
  function pintarModulos() {
    const linhas = Object.entries(S.saude).map(([n, s]) => `<tr>
      <td class="mono">${esc(n)}</td><td class="mono">${s.alvos_total}</td>
      <td class="mono">${s.alvos_falha}</td><td class="mono">${s.duracao_s} s</td>
      <td class="mono">${s.rejeitadas}</td>
      <td>${s.ultima_coleta_ok
        ? `<span class="selo verde">${esc(s.ultima_coleta_ok.slice(11, 19))}</span>`
        : `<span class="selo vermelho">nunca</span>`}</td></tr>`).join("");
    $("centro").innerHTML = `<div class="grade larga"><section class="cartao">
      <h2>Coleta</h2><div class="corpo sem"><div class="rolagem"><table>
      <thead><tr><th>Módulo</th><th>Alvos</th><th>Falhas</th><th>Duração</th>
        <th>Recusadas</th><th>Última coleta ok</th></tr></thead>
      <tbody>${linhas || `<tr><td colspan="6" class="nulo">nenhum módulo reportou</td></tr>`}</tbody>
      </table></div></div></section></div>`;
  }

  function pintarCobertura() {
    const linhas = S.sinais.map((s) => `<tr>
      <td class="mono">${esc(s.familia)}</td>
      <td>${s.disponivel ? `<span class="selo verde">coletando</span>`
                         : `<span class="selo neutro">sem coletor</span>`}</td>
      <td>${esc(s.motivo || "—")}</td></tr>`).join("");
    $("centro").innerHTML = `<div class="grade larga"><section class="cartao">
      <h2>Cobertura por família</h2><div class="corpo sem"><div class="rolagem"><table>
      <thead><tr><th>Família</th><th>Estado</th><th>Motivo</th></tr></thead>
      <tbody>${linhas}</tbody></table></div></div></section></div>`;
  }

  function pintarCadastro() {
    const titulos = {
      conflitos: "MACs repetidos entre ativos", homonimos: "Homônimos desambiguados",
      divergencias: "Nome discorda do endereço", papel_desconhecido: "Papel não reconhecido",
      fora_do_padrao: "Nome fora do padrão",
    };
    const cartoes = Object.entries(titulos).map(([k, t]) => {
      const itens = S.achados[k] || [];
      if (!itens.length) return "";
      return `<section class="cartao"><h2>${t} · ${itens.length}</h2>
        <div class="corpo sem"><div class="rolagem"><table><tbody>
        ${itens.slice(0, 20).map((i) => `<tr><td class="nome">${esc(i)}</td></tr>`).join("")}
        ${itens.length > 20 ? `<tr><td class="nulo">… mais ${itens.length - 20}</td></tr>` : ""}
        </tbody></table></div></div></section>`;
    }).join("");
    $("centro").innerHTML = cartoes
      ? `<div class="grade">${cartoes}</div>`
      : `<p class="vazio">nenhum achado</p>`;
  }

  /* ============================== fluxo ================================== */
  async function ficha(id) {
    if (!S.fichas.has(id)) S.fichas.set(id, await api(`/api/v1/ativos/${encodeURIComponent(id)}`));
    return S.fichas.get(id);
  }

  async function pintarAba() {
    if (S.aba === "modulos") return pintarModulos();
    if (S.aba === "cobertura") return pintarCobertura();
    if (S.aba === "cadastro") return pintarCadastro();
    if (!S.sel) return;
    const f = await ficha(S.sel);
    pintarArvore();
    return S.aba === "dispositivos" ? pintarDispositivos(f) : pintarAtivo(f);
  }

  function marcarAba() {
    document.querySelectorAll(".abas button").forEach((b) =>
      b.setAttribute("aria-current", String(b.dataset.aba === S.aba)));
  }

  async function atualizar() {
    const [resumo, ativos, sinais, achados] = await Promise.all([
      api("/api/v1/resumo"), api("/api/v1/ativos"), api("/api/v1/sinais"), api("/api/v1/achados"),
    ]);
    Object.assign(S, { resumo, ativos, sinais, achados, saude: resumo.modulos || {} });
    if (!S.sel && ativos.length) {
      S.sel = ativos.reduce((a, b) =>
        (b.dispositivos.length > a.dispositivos.length ? b : a), ativos[0]).ativo_id;
    }
    pintarTopo();
    pintarArvore();
    await pintarAba();
    $("rodape").textContent =
      `${resumo.ativos} ativos · ${resumo.dispositivos} dispositivos · ${resumo.arestas_abertas} arestas`;
    $("versao").textContent = `atualizado ${new Date().toLocaleTimeString("pt-BR")}`;
  }

  $("arvore").addEventListener("click", async (e) => {
    const a = e.target.closest("[data-ativo]");
    if (a) { S.sel = a.dataset.ativo; if (S.aba !== "dispositivos") S.aba = "ativo";
             marcarAba(); await pintarAba(); return; }
    const g = e.target.closest("[data-frota]");
    if (g) { const f = g.dataset.frota;
             S.abertos.has(f) ? S.abertos.delete(f) : S.abertos.add(f); pintarArvore(); }
  });

  document.querySelector(".abas").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-aba]");
    if (!b) return;
    S.aba = b.dataset.aba; marcarAba(); await pintarAba();
  });

  $("centro").addEventListener("click", async (e) => {
    if (e.target.closest("[data-foto-ativo]")) {
      escolherAbrangenciaAtivo((await ficha(S.sel)).ativo); return;
    }
    const d = e.target.closest("[data-foto-disp]");
    if (d) {
      const f = await ficha(S.sel);
      const alvo = f.dispositivos.find((x) => x.chave === d.dataset.fotoDisp);
      if (alvo) escolherAbrangencia(alvo);
    }
  });

  let t;
  $("busca").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => { S.filtro = e.target.value.trim(); pintarArvore(); }, 120);
  });

  atualizar().catch((erro) => {
    $("centro").innerHTML = `<p class="vazio">Sem resposta da API: ${esc(erro.message)}</p>`;
  });
  setInterval(() => atualizar().catch(() => {}), 30000);
})();
