package br.com.mina.navegacao

import android.app.Application

/**
 * Existe por um motivo so': instalar o registro de falhas antes de qualquer
 * Activity. Erro no arranque da tela acontece dentro do super.onCreate, fora
 * do alcance de um try/catch nosso — daqui ele ainda e' gravado.
 */
class NavegacaoApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Falhas.instalar(this)
    }
}
