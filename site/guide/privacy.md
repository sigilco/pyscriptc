---
title: Privacy notice
description: Who processes pyscriptc usage data, why, for how long, and your rights.
---

This is a plain-language notice for pyscriptc usage telemetry. It is a draft for the maintainer to review; it is not legal advice.

## Controller

Controller: [PLACEHOLDER: name of the person or entity responsible]
Contact: [PLACEHOLDER: EU postal address and email for privacy requests]

## What is collected

A random install id, the pyscriptc and Python (major.minor) versions, the OS family, the subcommand you ran, and for `compile` and `coverage` an outcome, a duration and aggregate fence counts with diagnostic family names.
The exact field list is the table in the [telemetry guide](/guide/telemetry/).

## What is not collected

Source code, file names and paths, user names, host names, machine ids, MAC addresses, imported module names, environment variables and argument values.
Nothing is read from your hardware to build the id.
Every event asks PostHog not to store the client IP, not to geolocate and not to create a person profile.

## Why, and the legal basis

Purpose: find out which unsupported constructs block users the most, to prioritise the roadmap.
Legal basis: [DECISION FOR THE MAINTAINER: consent (GDPR Art. 6(1)(a), requires opt-in) or legitimate interest (Art. 6(1)(f), opt-out with a notice and a documented balancing test)].
The current shipped default is opt-out with a first-run notice; the opt-in switch exists and is documented in the pull request that introduced this page.

## Who processes it

PostHog (EU cloud, `eu.i.posthog.com`) as processor, under its data processing agreement. [PLACEHOLDER: confirm the DPA is signed and the project lives in the EU region.]

## Retention

Raw events are kept for 90 days, then deleted. [PLACEHOLDER: set the matching data retention in the PostHog project settings so this statement is true.]

## Your rights

You can ask for access to, erasure of, or to object to processing of your data, and withdraw consent if consent is the basis.
Because the id is random and stored only on your machine, we can only find your events if you tell us the id: run `cat ~/.config/pyscriptc/telemetry-id` (or `$XDG_CONFIG_HOME/pyscriptc/telemetry-id`) and send it to the contact above with your request.
Without the id we cannot link any event to you, and the data is not otherwise attributable.
You can also stop all future collection yourself: `pyscriptc telemetry disable`, `PYSC_NO_TELEMETRY=1` or `DO_NOT_TRACK=1`, and `pyscriptc telemetry reset-id` to cut the link to past events.
You may complain to your national data protection authority.
