package br.com.mina.navegacao

/**
 * O minimo de JSON que o app precisa escrever e ler.
 *
 * A interface consome GeoJSON de centenas de KB, mas o app nunca interpreta
 * isso: repassa os bytes como vieram do servidor, igual ao cliente do PTX.
 * Aqui so' se monta as respostas pequenas e se pesca um campo de texto.
 */
object Json {

    fun escapa(s: String?): String {
        if (s == null) return ""
        val b = StringBuilder(s.length + 8)
        for (c in s) when (c) {
            '\\' -> b.append("\\\\")
            '"' -> b.append("\\\"")
            '\n', '\r', '\t' -> b.append(' ')
            else -> if (c.code < 0x20) b.append(' ') else b.append(c)
        }
        return b.toString()
    }

    fun num(v: Double): String {
        if (v.isNaN() || v.isInfinite()) return "0"
        val arredondado = Math.round(v * 1_000_000.0) / 1_000_000.0
        return if (arredondado == Math.floor(arredondado) && Math.abs(arredondado) < 1e15)
            arredondado.toLong().toString()
        else arredondado.toString()
    }

    /** Pesca um campo de texto sem montar um parser inteiro. */
    fun campo(json: String?, nome: String): String {
        if (json == null) return ""
        val chave = "\"$nome\""
        var i = json.indexOf(chave)
        if (i < 0) return ""
        i = json.indexOf(':', i)
        if (i < 0) return ""
        val a = json.indexOf('"', i)
        if (a < 0) return ""
        val b = json.indexOf('"', a + 1)
        if (b < 0) return ""
        return json.substring(a + 1, b)
    }

    fun erro(msg: String): String = """{"ok":false,"erro":"${escapa(msg)}"}"""
}
