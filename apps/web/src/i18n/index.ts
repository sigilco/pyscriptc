import * as m from "../paraglide/messages.js";
import { getLocale, setLocale, locales, baseLocale } from "../paraglide/runtime.js";

export { m, getLocale, setLocale, locales, baseLocale };
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = baseLocale;

export function useI18n() {
  return {
    nav: {
      logoAlt: m.nav_logo_alt(),
      playground: m.nav_playground(),
      playgroundBadge: m.nav_playground_badge(),
      docs: m.nav_docs(),
      compat: m.nav_compat(),
      github: m.nav_github(),
      installCommand: m.nav_install_cmd(),
      copied: m.nav_copied(),
      langLabel: m.nav_lang_label(),
      langSelect: m.nav_lang_select(),
    },
    hero: {
      badge: m.hero_badge(),
      titleLine1: m.hero_title_line1(),
      titleLine2: m.hero_title_line2(),
      subtitle: m.hero_subtitle(),
      docsCta: m.hero_docs_cta(),
      compatCta: m.hero_compat_cta(),
      installLabel: m.hero_install_label(),
      installCommand: m.hero_install_cmd(),
    },
    metrics: [
      {
        val: m.metric_1_val(),
        unit: m.metric_1_unit(),
        label: m.metric_1_label(),
        desc: m.metric_1_desc(),
      },
      {
        val: m.metric_2_val(),
        unit: m.metric_2_unit(),
        label: m.metric_2_label(),
        desc: m.metric_2_desc(),
      },
      {
        val: m.metric_3_val(),
        unit: m.metric_3_unit(),
        label: m.metric_3_label(),
        desc: m.metric_3_desc(),
      },
      {
        val: m.metric_4_val(),
        unit: m.metric_4_unit(),
        label: m.metric_4_label(),
        desc: m.metric_4_desc(),
      },
    ],
    parity: {
      badge: m.parity_badge(),
      title: m.parity_title(),
      subtitle: m.parity_subtitle(),
      terminalFile: m.parity_terminal_file(),
      terminalTag: m.parity_terminal_tag(),
      terminalRun: m.parity_terminal_run(),
      terminalStep1: m.parity_terminal_step1(),
      terminalStep2: m.parity_terminal_step2(),
      terminalExec: m.parity_terminal_exec(),
      terminalPerf: m.parity_terminal_perf(),
      terminalVs: m.parity_terminal_vs(),
      guaranteeTitle: m.parity_guarantee_title(),
      guaranteeTag: m.parity_guarantee_tag(),
      guaranteeDesc: m.parity_guarantee_desc(),
      semTitle: m.parity_sem_title(),
      semDesc: m.parity_sem_desc(),
      diagTitle: m.parity_diag_title(),
      diagDesc: m.parity_diag_desc(),
    },
    surfaces: {
      title: m.surfaces_title(),
      subtitle: m.surfaces_subtitle(),
      items: [
        {
          num: m.surface_1_num(),
          tag: m.surface_1_tag(),
          title: m.surface_1_title(),
          desc: m.surface_1_desc(),
          meta1: { k: m.surface_1_runtime_k(), v: m.surface_1_runtime_v() },
          meta2: { k: m.surface_1_format_k(), v: m.surface_1_format_v() },
        },
        {
          num: m.surface_2_num(),
          tag: m.surface_2_tag(),
          title: m.surface_2_title(),
          desc: m.surface_2_desc(),
          meta1: { k: m.surface_2_target_k(), v: m.surface_2_target_v() },
          meta2: { k: m.surface_2_integration_k(), v: m.surface_2_integration_v() },
        },
        {
          num: m.surface_3_num(),
          tag: m.surface_3_tag(),
          title: m.surface_3_title(),
          desc: m.surface_3_desc(),
          meta1: { k: m.surface_3_arch_k(), v: m.surface_3_arch_v() },
          meta2: { k: m.surface_3_deployment_k(), v: m.surface_3_deployment_v() },
        },
      ],
    },
    crossCompile: {
      badge: m.cross_badge(),
      title: m.cross_title(),
      subtitle: m.cross_subtitle(),
    },
    cta: {
      title: m.cta_title(),
      subtitle: m.cta_subtitle(),
      primaryBtn: m.cta_primary(),
      secondaryBtn: m.cta_secondary(),
      sponsorBtn: m.cta_sponsor(),
    },
    footer: {
      tagline: m.footer_tagline(),
      copyright: m.footer_copyright(),
      status: m.footer_status(),
      sponsor: m.footer_sponsor(),
    },
    playgroundModal: {
      title: m.modal_title(),
      desc: m.modal_desc(),
      cmd: m.modal_cmd(),
      close: m.modal_close(),
    },
  };
}
