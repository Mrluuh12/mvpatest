package br.com.mina.navegacao

import android.webkit.WebResourceResponse
import java.io.ByteArrayInputStream
import java.net.URLDecoder

/**
 * Responde as requisicoes que a interface faz para /pos, /malha, /frota e
 * companhia.
 *
 * E' o que permite usar a MESMA interface.html do PTX, sem tocar numa linha:
 * la' quem responde e' o HttpListener do PtxNav.exe; aqui e' o WebView, pelo
 * shouldInterceptRequest. Sem abrir socket, sem porta, sem servidor no
 * aparelho.
 */
class Api(
    private val posicao: Posicao,
    private val sinc: Sincronizacao,
    private val config: Config
) {

    /** Pilha da execucao anterior, quando houve. Aparece no menu, em Estado. */
    var ultimaFalha: String? = null

    /** Versao e momento do build, para o menu. */
    var versaoApp: String = ""

    fun responde(caminho: String, consulta: String?): WebResourceResponse? {
        // a tela vem com ?v=<instalacao> para furar o cache do WebView; ela e'
        // servida pelo carregador de assets, nao por aqui
        if (caminho == "/interface.html" || caminho == "/") return null

        // foto aerea: bytes, nao JSON. Vem do cache em disco ou do servidor.
        if (caminho.startsWith("/foto/")) {
            val rel = caminho.removePrefix("/foto/")
            val dados = sinc.foto(rel)
                ?: return WebResourceResponse(
                    "text/plain", "utf-8", 404, "sem ladrilho",
                    mapOf("Access-Control-Allow-Origin" to "*"),
                    ByteArrayInputStream(ByteArray(0)))
            val tipo = when {
                rel.endsWith(".json") -> "application/json"
                rel.endsWith(".png") -> "image/png"
                else -> "image/jpeg"
            }
            return WebResourceResponse(
                tipo, if (rel.endsWith(".json")) "utf-8" else null,
                ByteArrayInputStream(dados))
        }
        val corpo: String = when (caminho) {
            "/pos" -> posicao.json(config.callsign)
            "/estado" -> sinc.estadoJson(posicao.descricao(), ultimaFalha, versaoApp)
            "/malha" -> ouVazio(sinc.malha())
            "/locais" -> ouVazio(sinc.locais())
            "/areas" -> ouVazio(sinc.areas())
            "/frota" -> sinc.frotaAoVivo()
            "/desmontes" -> sinc.desmontes()
            "/rota" -> rota(consulta)
            "/config" -> Json.erro("use POST")
            else -> return null      // qualquer outra coisa vem dos assets
        }
        return json(corpo)
    }

    /**
     * A interface pede a rota pelo nome do destino; quem sabe a posicao do
     * veiculo e' o app. Ele completa a origem e repassa ao servidor, igual ao
     * cliente do PTX.
     */
    private fun rota(consulta: String?): String {
        if (!posicao.temFix) {
            return """{"ok":false,"erro":"sem posicao GPS valida",""" +
                   """"detalhe":"o tablet ainda nao tem fix; a rota precisa saber onde voce esta"}"""
        }
        val p = parametros(consulta)
        val equipamento = p["equip"] ?: ""
        val destino = p["para"] ?: ""
        if (equipamento.isEmpty() && destino.isEmpty())
            return Json.erro("escolha um destino")

        val origem = "de_lat=${Json.num(posicao.lat)}&de_lon=${Json.num(posicao.lon)}"
        val alvo = if (equipamento.isNotEmpty())
            "para_equip=" + codifica(equipamento)
        else
            "para=" + codifica(destino)
        return sinc.rota("/api/rota?$origem&$alvo")
    }

    /** Troca de servidor pelo menu, sem precisar mexer no aparelho. */
    fun mudaConfig(corpo: String): String {
        var url = Json.campo(corpo, "servidor").trim().trimEnd('/')
        if (url.isEmpty()) return Json.erro("endereco vazio")
        if (!url.startsWith("http://") && !url.startsWith("https://")) url = "http://$url"
        return try {
            java.net.URL(url)
            config.servidor = url
            """{"ok":true,"servidor":"${Json.escapa(url)}"}"""
        } catch (e: Exception) {
            Json.erro("endereco invalido")
        }
    }

    private fun ouVazio(s: String): String =
        if (s.isEmpty()) Json.erro("sem dados em cache; aguarde a sincronizacao") else s

    private fun parametros(consulta: String?): Map<String, String> {
        if (consulta.isNullOrEmpty()) return emptyMap()
        val m = HashMap<String, String>()
        for (par in consulta.split("&")) {
            val i = par.indexOf('=')
            if (i <= 0) continue
            try {
                m[par.substring(0, i)] =
                    URLDecoder.decode(par.substring(i + 1), "UTF-8")
            } catch (_: Exception) {
            }
        }
        return m
    }

    private fun codifica(v: String): String =
        java.net.URLEncoder.encode(v, "UTF-8").replace("+", "%20")

    private fun json(corpo: String) = WebResourceResponse(
        "application/json", "utf-8",
        ByteArrayInputStream(corpo.toByteArray(Charsets.UTF_8))
    ).apply {
        responseHeaders = mapOf("Cache-Control" to "no-store",
                                "Access-Control-Allow-Origin" to "*")
    }
}
