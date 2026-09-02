import 'vuetify/styles'
import { createVuetify } from 'vuetify'
import {
  VApp, VMain, VContainer, VRow, VCol,
  VBtn, VTextField, VCard, VCardTitle, VCardItem,
  VCardText, VCardActions, VForm, VChip, VFileInput, VCheckbox,
  VDataTable, VIcon, VDivider, VAvatar, VSnackbar, VSheet, VSlider, VRangeSlider, VDatePicker, VSelect, VMenu, VAlert, VDialog, VProgressCircular, VProgressLinear, VSwitch,
  VNavigationDrawer, VList, VListItem, VListItemTitle, VListItemSubtitle, VImg, VExpansionPanels, VExpansionPanel, VExpansionPanelText,
  VAppBar, VAppBarTitle, VToolbar, VToolbarTitle, VSpacer, VFooter, VTooltip, VListSubheader, VOverlay, VVirtualScroll, VBadge,
   VTable, VChipGroup, VTabs, VTab, VWindow, VWindowItem
} from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { aliases, mdi } from 'vuetify/iconsets/mdi'
import { h } from 'vue'
import LotVultureIcon from '../components/LotVultureIcon.vue'

const custom = {
  component: (props) => h(LotVultureIcon, props)
}

export default createVuetify({
  components: {
    VApp,
    VMain,
    VContainer,
    VRow,
    VCol,
    VBtn,
    VTextField,
    VCard,
    VCardTitle,
    VCardItem,
    VCardText,
    VCardActions,
    VForm,
    VFileInput,
    VNavigationDrawer,
    VList,
    VListItem,
    VListItemTitle,
    VListItemSubtitle,
    VImg,
    VAppBar,
    VAppBarTitle,
    VToolbar,
    VToolbarTitle,
    VDataTable,
    VIcon,
    VDivider,
    VChip,
    VAvatar,
    VCheckbox,
    VSnackbar,
    VSpacer,
    VSheet,
    VSlider,
    VRangeSlider,
    VDatePicker,
    VSelect,
    VMenu,
    VAlert,
    VDialog,
    VProgressCircular,
    VProgressLinear,
    VSwitch,
    VExpansionPanels,
    VExpansionPanel,
    VExpansionPanelText,
    VFooter,
    VTooltip,
    VListSubheader,
    VOverlay,
    VVirtualScroll,
    VBadge,
    VTabs,
    VTab,
    VWindow,
    VWindowItem,
    VTable,
    VChipGroup
  },
  directives,
  icons: {
    defaultSet: 'mdi',
    aliases: {
      ...aliases,
      'lot-vulture': custom
    },
    sets: { 
      mdi,
      custom
    }
  }
})
