// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import java.io.IOException
import java.io.InputStream
import java.net.ConnectException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket

/**
 * How this app comes by a gramarye env's token, and learns whether it still opens the env.
 *
 * The token lets its holder start anything in the env, and any app on the phone can reach the
 * agent's port, so the agent gives it out once, for a six-digit code that root asked it for in
 * the env a moment ago (`tessera-pair`) and the user typed here. See pair() in gramarye/agent.py.
 */
object AohpPairing {
    sealed interface Paired {
        data class Done(val target: AohpTarget) : Paired
        /** The agent said no, and [why], in words for the user. */
        data class Refused(val why: String) : Paired
        /** No agent that knows pairing answered; [why] in words for the user. */
        data class NoAnswer(val why: String) : Paired
    }

    sealed interface Pinged {
        /** The token opens the env, which can now do what [target] says. */
        data class Answers(val target: AohpTarget) : Pinged
        /** An agent is there and doesn't take this token, or is too old to be asked. */
        data object Refused : Pinged
        /** Nothing listens on the port: the env is down, or its agent is. */
        data object Silent : Pinged
    }

    const val CODE_LENGTH = 6

    /** The code in what the user typed ("123 456", "123-456"), or null when that isn't one. */
    fun code(typed: String): String? {
        if (typed.any { !it.isDigit() && it != ' ' && it != '-' }) return null
        return typed.filter { it.isDigit() }.takeIf { it.length == CODE_LENGTH }
    }

    fun pair(code: String, port: Int = AohpTarget.DEFAULT_PORT): Paired = try {
        pairAnswer(ask(port, "TESSERAPAIR $code"))
    } catch (_: ConnectException) {
        Paired.NoAnswer("Nothing answers on port $port. Is the env running, and Tessera's agent installed in it?")
    } catch (e: IOException) {
        Paired.NoAnswer("Couldn't reach the env's agent: ${e.message ?: e.javaClass.simpleName}")
    }

    /** What the agent's [answer] to a pairing code means; null: it hung up without one. */
    fun pairAnswer(answer: String?): Paired = when {
        answer == null ->
            Paired.NoAnswer("The env's agent doesn't know pairing. Update it: run gramarye/install.sh in the env.")
        answer.startsWith("OK ") ->
            AohpTarget.parse(answer.removePrefix("OK "))?.let { Paired.Done(it) }
                ?: Paired.NoAnswer("The env's agent answered something this app doesn't understand.")
        answer.startsWith("NO ") -> Paired.Refused(answer.removePrefix("NO ").replaceFirstChar { it.uppercase() } + ".")
        else -> Paired.NoAnswer("The env's agent answered something this app doesn't understand.")
    }

    fun ping(target: AohpTarget): Pinged = try {
        pingAnswer(target, ask(target.port, "TESSERAPING ${target.token}"))
    } catch (_: ConnectException) {
        Pinged.Silent
    } catch (_: IOException) {
        Pinged.Silent
    }

    /** What the agent's [answer] to a ping means for [target]; null: it hung up without one. */
    fun pingAnswer(target: AohpTarget, answer: String?): Pinged {
        if (answer == null || (answer != "PONG" && !answer.startsWith("PONG "))) return Pinged.Refused
        val caps = answer.removePrefix("PONG").trim()
        // "-": an agent that was never told what its env can do. Keep what pairing said.
        return Pinged.Answers(if (caps == "-") target else target.withCaps(caps) ?: target)
    }

    /** Sends [line] to the agent on [port] and returns the line it answers, null when it just hangs up. */
    private fun ask(port: Int, line: String): String? {
        Socket().use { socket ->
            // By number: the agent listens on 127.0.0.1 alone, and Android's "loopback address" is ::1.
            socket.connect(InetSocketAddress(InetAddress.getByAddress(byteArrayOf(127, 0, 0, 1)), port), CONNECT_MS)
            socket.soTimeout = ANSWER_MS
            socket.getOutputStream().apply {
                write("$line\n".toByteArray(Charsets.US_ASCII))
                flush()
            }
            return readLine(socket.getInputStream())
        }
    }

    private fun readLine(input: InputStream): String? {
        val line = StringBuilder()
        while (line.length < 1024) {
            val b = input.read()
            if (b < 0) return line.toString().takeIf { it.isNotEmpty() }
            if (b == '\n'.code) return line.toString()
            line.append(b.toChar())
        }
        return line.toString()
    }

    private const val CONNECT_MS = 3_000
    private const val ANSWER_MS = 5_000
}
