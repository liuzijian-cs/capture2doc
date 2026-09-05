package io.github.liuzijiancs.capture2doc.ui.home

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import io.github.liuzijiancs.capture2doc.data.settings.ConnectionSettings
import io.github.liuzijiancs.capture2doc.data.task.HttpDocumentService
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun ConnectionSettingsScreen(settings: ConnectionSettings, onBack: () -> Unit, onSaved: () -> Unit) {
    var address by remember { mutableStateOf(settings.state.value.baseUrl.ifBlank { "https://c2d.liu.zj.cn" }) }
    // Deliberately not saveable: credentials must not enter instance-state bundles.
    var token by remember { mutableStateOf(settings.token(address)) }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    BackHandler { onBack() }
    Scaffold(topBar = { TopAppBar(title = { Text("连接设置") }, navigationIcon = { TextButton(onClick = onBack) { Text("返回") } }) }) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text("未连接时仍可拍摄。保存后会继续同步对应服务的本地任务。", style = MaterialTheme.typography.bodyMedium)
            OutlinedTextField(address, onValueChange = {
                address = it
                token = runCatching { settings.token(it) }.getOrDefault("")
                message = null
            }, label = { Text("服务地址") }, singleLine = true, enabled = !busy, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth().testTag("connection_address"))
            OutlinedTextField(token, onValueChange = { token = it; message = null }, label = { Text("设备令牌") },
                visualTransformation = PasswordVisualTransformation(), singleLine = true, enabled = !busy,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                modifier = Modifier.fillMaxWidth().testTag("connection_token"))
            Text("令牌由 Android Keystore 加密，按服务地址保存在本机。修改默认地址不会迁移已绑定任务。", style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedButton(enabled = !busy, onClick = {
                    busy = true; message = "正在测试连接…"
                    scope.launch {
                        try { HttpDocumentService({ token }).testConnection(address); message = "连接成功，接口兼容" }
                        catch (e: CancellationException) { throw e }
                        catch (e: Exception) { message = e.message ?: "连接失败" }
                        finally { busy = false }
                    }
                }) { Text("测试连接") }
                Button(enabled = !busy, onClick = {
                    busy = true
                    scope.launch {
                        try { settings.save(address, token); token = ""; onSaved() }
                        catch (e: CancellationException) { throw e }
                        catch (e: Exception) { message = e.message ?: "保存失败" }
                        finally { busy = false }
                    }
                }, modifier = Modifier.testTag("connection_save")) { Text("保存") }
            }
            message?.let { Text(it, modifier = Modifier.testTag("connection_message")) }
        }
    }
}
