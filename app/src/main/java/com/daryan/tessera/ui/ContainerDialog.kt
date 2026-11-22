// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp

/**
 * Where an app runs in gramarye mode, from its long-press menu: the env itself, or one of the
 * Droidspaces containers the env's agent offers ([offered]). The app has to be installed where
 * it is sent: the launcher lists what the env has, and doesn't look inside the containers.
 * Changes apply from the app's next launch.
 */
@Composable
fun ContainerDialog(
    appName: String,
    /** The container the app runs in now; null: the env. */
    current: String?,
    offered: List<String>,
    /** Saves the choice (null: the env); returns what's wrong with it, and then nothing is saved. */
    onSave: (String?) -> List<String>,
    onDismiss: () -> Unit,
) {
    var chosen by remember(current) { mutableStateOf(current) }
    var problems by remember(current) { mutableStateOf(emptyList<String>()) }
    // The app's own container stays in the list while it isn't offered (stopped, say), so
    // opening this doesn't quietly move the app.
    val names = remember(offered, current) { (listOfNotNull(current) + offered).distinct() }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Where $appName runs") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Choice("Gramarye env", chosen == null) { chosen = null; problems = emptyList() }
                names.forEach { name ->
                    val gone = name !in offered
                    Choice(if (gone) "$name (not running)" else name, chosen == name) { chosen = name; problems = emptyList() }
                }
                if (problems.isNotEmpty()) {
                    Text(
                        problems.joinToString("\n"),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
                Text(
                    "A container is a Droidspaces system on this phone. The app has to be installed " +
                        "in it, and it must be running when the app opens.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    "Changes apply the next time the app opens.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = {
                problems = onSave(chosen)
                if (problems.isEmpty()) onDismiss()
            }) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun Choice(label: String, selected: Boolean, onSelect: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().selectable(selected = selected, role = Role.RadioButton, onClick = onSelect),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        RadioButton(selected = selected, onClick = null)
        Text(label, style = MaterialTheme.typography.bodyLarge)
    }
}
