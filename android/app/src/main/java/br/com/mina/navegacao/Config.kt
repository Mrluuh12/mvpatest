package br.com.mina.navegacao

import android.content.Context
import android.os.Build

/** Endereco do servidor e nome do equipamento, guardados entre execucoes. */
class Config(ctx: Context) {

    companion object {
        private const val ARQUIVO = "navegacao"
        private const val PADRAO = "http://10.188.111.249:5000"
    }

    private val prefs = ctx.getSharedPreferences(ARQUIVO, Context.MODE_PRIVATE)

    var servidor: String
        get() = prefs.getString("servidor", PADRAO) ?: PADRAO
        set(v) = prefs.edit().putString("servidor", v.trimEnd('/')).apply()

    /** Nome do equipamento. Sem configurar, usa o nome do proprio tablet. */
    var callsign: String
        get() = prefs.getString("callsign", null)
            ?: (Build.MODEL ?: "TABLET").uppercase()
        set(v) = prefs.edit().putString("callsign", v).apply()

    var limiteParadoKmh: Double
        get() = prefs.getFloat("limite_parado", 3f).toDouble()
        set(v) = prefs.edit().putFloat("limite_parado", v.toFloat()).apply()
}
