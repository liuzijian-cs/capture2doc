package io.github.liuzijiancs.capture2doc.data.settings

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import android.util.Base64
import io.github.liuzijiancs.capture2doc.BuildConfig
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import org.json.JSONObject
import okhttp3.HttpUrl.Companion.toHttpUrl

internal fun normalizeServiceUrl(value: String): String {
    val url = value.trim().toHttpUrl()
    require(url.username.isEmpty() && url.password.isEmpty() && url.query == null && url.fragment == null) { "服务地址不能包含凭据、查询或片段" }
    require(url.isHttps || BuildConfig.DEBUG) { "正式版本仅支持 HTTPS" }
    return url.toString().trimEnd('/')
}

internal data class ConnectionConfiguration(val baseUrl: String = "", val configured: Boolean = false, val revision: Long = 0)

internal class ConnectionSettings(private val directory: File) {
    private val file = AtomicFile(File(directory, "connection-settings.enc"))
    private val lock = Any()
    private var loaded = false
    private var data = JSONObject()
    private val _state = MutableStateFlow(ConnectionConfiguration())
    val state = _state.asStateFlow()
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val alias = "capture2doc.connection.v1"
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    suspend fun initialize() = withContext(Dispatchers.IO) { synchronized(lock) {
        if (!loaded) {
            if (file.baseFile.exists() || File(file.baseFile.path + ".bak").exists()) {
                try {
                    val envelope = file.openRead().bufferedReader().use { JSONObject(it.readText()) }
                    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                    cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(envelope.getString("iv"), Base64.NO_WRAP)))
                    data = JSONObject(String(cipher.doFinal(Base64.decode(envelope.getString("data"), Base64.NO_WRAP)), Charsets.UTF_8))
                } catch (_: Exception) { throw IllegalStateException("无法读取连接配置，请在设置中重新保存") }
            }
            loaded = true
            publish()
        }
    } }
    private fun publish() {
        val url = data.optString("defaultUrl").ifBlank { BuildConfig.SERVICE_BASE_URL.trim().trimEnd('/') }
        _state.value = ConnectionConfiguration(url, token(url).isNotBlank(), _state.value.revision + 1)
    }
    fun token(baseUrl: String): String = synchronized(lock) {
        if (baseUrl.isBlank()) "" else data.optJSONObject("tokens")?.optString(normalizeServiceUrl(baseUrl)).orEmpty()
    }
    suspend fun save(baseUrl: String, token: String) = withContext(Dispatchers.IO) { synchronized(lock) {
        val url = normalizeServiceUrl(baseUrl)
        require(token.isNotBlank() && token.none { it <= ' ' || it.code > 126 }) { "设备令牌格式无效" }
        val next = JSONObject(data.toString()).put("defaultUrl", url)
        val tokens = next.optJSONObject("tokens") ?: JSONObject().also { next.put("tokens", it) }
        tokens.put(url, token)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val encrypted = cipher.doFinal(next.toString().toByteArray(Charsets.UTF_8))
        val envelope = JSONObject().put("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP)).put("data", Base64.encodeToString(encrypted, Base64.NO_WRAP))
        check(directory.exists() || directory.mkdirs())
        val stream = file.startWrite()
        try { stream.write(envelope.toString().toByteArray()); file.finishWrite(stream) }
        catch (e: Exception) { file.failWrite(stream); throw e }
        data = next
        loaded = true
        publish()
    } }
}
