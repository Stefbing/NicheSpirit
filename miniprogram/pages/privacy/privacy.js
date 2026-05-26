Page({
  data: {
    activeTab: 'service', // 'service' | 'privacy'
  },

  onLoad(query) {
    // 支持通过参数指定初始 Tab
    if (query.tab === 'privacy') {
      this.setData({ activeTab: 'privacy' });
    }
  },

  switchTab(e) {
    const tab = e.currentTarget.dataset.tab;
    this.setData({ activeTab: tab });
  },
});
