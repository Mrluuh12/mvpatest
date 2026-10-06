package br.com.mina.navegacao

import android.content.Context
import android.location.GnssStatus
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.Bundle
import android.os.Looper
import android.util.Log

/**
 * Posicao propria pelo GPS do proprio tablet.
 *
 * Usa o LocationManager do sistema, nao o FusedLocationProvider: tablet de
 * campo costuma vir sem Google Play Services, e o app nao pode depender disso
 * para funcionar na cava.
 *
 * O contrato com a interface e' o mesmo do PTX: se a ultima posicao tem mais
 * de [segundosSemFix] segundos, o fix deixa de valer. Navegar com posicao
 * velha e' pior que nao navegar.
 */
class Posicao(private val ctx: Context) {

    companion object {
        private const val TAG = "Posicao"
        const val SEGUNDOS_SEM_FIX = 10L
    }

    @Volatile var lat = 0.0; private set
    @Volatile var lon = 0.0; private set
    @Volatile var cog = 0.0; private set
    @Volatile var velKmh = 0.0; private set
    @Volatile var altitude = 0.0; private set
    @Volatile var precisaoM = 0.0; private set
    @Volatile var satelites = 0; private set
    @Volatile var quandoMs = 0L; private set
    @Volatile var estado = "aguardando o GPS"; private set
    @Volatile var provedor = ""; private set

    var limiteParadoKmh = 3.0

    private val gerente by lazy {
        ctx.getSystemService(Context.LOCATION_SERVICE) as LocationManager
    }

    private val ouvinte = object : LocationListener {
        override fun onLocationChanged(l: Location) = registra(l)
        override fun onProviderEnabled(p: String) { estado = "GPS ligado" }
        override fun onProviderDisabled(p: String) { estado = "GPS desligado no tablet" }
        @Deprecated("exigido em API antiga")
        override fun onStatusChanged(p: String?, s: Int, e: Bundle?) {}
    }

    private val contadorSatelites = object : GnssStatus.Callback() {
        override fun onSatelliteStatusChanged(status: GnssStatus) {
            var usados = 0
            for (i in 0 until status.satelliteCount) if (status.usedInFix(i)) usados++
            satelites = usados
        }
    }

    /**
     * Chamar depois que a permissao de localizacao foi concedida.
     *
     * Pede posicao ao GPS E a rede. So' o GPS serve para navegar na cava, mas
     * dentro de predio ele nao pega ceu e nunca da' fix — e o app fica dizendo
     * "sem posicao" numa mesa, sem jeito de conferir se funciona. A rede
     * preenche esse vazio; assim que o GPS entrega, ele assume.
     */
    fun iniciar() {
        var algum = false
        for (p in listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)) {
            try {
                if (!gerente.isProviderEnabled(p)) continue
                // 1 s e 0 m: o operador quer ver a seta andando, nao economizar
                gerente.requestLocationUpdates(p, 1000L, 0f, ouvinte,
                                               Looper.getMainLooper())
                algum = true
                gerente.getLastKnownLocation(p)?.let {
                    if (System.currentTimeMillis() - it.time < 120_000) registra(it)
                }
            } catch (e: SecurityException) {
                estado = "sem permissao de localizacao"
                Log.w(TAG, "sem permissao em $p", e)
                return
            } catch (e: Exception) {
                Log.w(TAG, "falhou pedir posicao a $p", e)
            }
        }
        if (!algum) {
            estado = "localizacao desligada no tablet"
            return
        }
        if (!temFix) estado = "procurando posicao"
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                gerente.registerGnssStatusCallback(contadorSatelites, null)
            }
        } catch (e: Exception) {
            Log.w(TAG, "sem contagem de satelites", e)
        }
    }

    /**
     * Chamar quando a permissao de localizacao foi negada.
     *
     * Sem a permissao o LocationManager nunca entrega nada, e a tela ficaria
     * "procurando posicao" para sempre. Dizer onde resolver e' a diferenca
     * entre trinta segundos nos Ajustes e uma tarde procurando defeito no
     * tablet.
     */
    fun semPermissao() {
        parar()
        estado = "sem permissao de localizacao — Ajustes > Apps > " +
                 "Navegacao da Mina > Permissoes > Localizacao"
    }

    fun parar() {
        try {
            gerente.removeUpdates(ouvinte)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                gerente.unregisterGnssStatusCallback(contadorSatelites)
            }
        } catch (_: Exception) {
        }
    }

    private fun registra(l: Location) {
        // GPS manda enquanto estiver fresco: a posicao da rede tem centenas de
        // metros de erro, e para navegar em mina isso nao serve
        val ehGps = l.provider == LocationManager.GPS_PROVIDER
        if (!ehGps && provedor == LocationManager.GPS_PROVIDER &&
            System.currentTimeMillis() - quandoMs < 15_000) return
        provedor = l.provider ?: ""
        lat = l.latitude
        lon = l.longitude
        altitude = if (l.hasAltitude()) l.altitude else 0.0
        precisaoM = if (l.hasAccuracy()) l.accuracy.toDouble() else 0.0
        // m/s -> km/h; rumo so' vale com o tablet em movimento
        velKmh = if (l.hasSpeed()) l.speed * 3.6 else 0.0
        if (l.hasBearing() && velKmh > 1.0) cog = l.bearing.toDouble()
        quandoMs = System.currentTimeMillis()
        estado = "posicao boa"
    }

    val idadeS: Double
        get() = if (quandoMs == 0L) -1.0
                else (System.currentTimeMillis() - quandoMs) / 1000.0

    val temFix: Boolean
        get() = quandoMs != 0L && idadeS in 0.0..SEGUNDOS_SEM_FIX.toDouble()

    val parado: Boolean get() = velKmh <= limiteParadoKmh

    /** Mesmo JSON que o PTX serve em /pos: a interface e' a mesma nos dois. */
    fun json(callsign: String): String {
        val f = temFix
        return """{"callsign":"${Json.escapa(callsign)}",""" +
               """"lat":${Json.num(lat)},"lon":${Json.num(lon)},""" +
               """"cog":${Json.num(cog)},"sog_kmh":${Json.num(velKmh)},""" +
               """"altitude":${Json.num(altitude)},"precisao_m":${Json.num(precisaoM)},""" +
               """"satelites":$satelites,"qualidade":${if (f) 1 else 0},""" +
               """"idade_s":${Json.num(idadeS)},"tem_fix":$f,""" +
               """"parado":$parado,"limite_parado_kmh":${Json.num(limiteParadoKmh)}}"""
    }

    /** Texto curto para a linha "Fonte" do menu. */
    fun descricao(): String {
        if (!temFix) return estado
        val p = if (precisaoM > 0) " · ${precisaoM.toInt()} m" else ""
        val s = if (satelites > 0) " · $satelites sat" else ""
        return when (provedor) {
            LocationManager.GPS_PROVIDER -> "GPS interno$s$p"
            LocationManager.NETWORK_PROVIDER -> "rede (sem GPS ainda)$p"
            else -> "posicao$p"
        }
    }
}
