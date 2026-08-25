// Stash Performer ID — Step 2 image tagger page (UI-only plugin, plain JS over window.PluginApi).
//
// Lists images with the same filter/sort/pagination surface as the native grid (tags / path /
// organized), and per image: Scrape (via our metadata-provider scraper) -> pick or create a
// performer (PerformerSelect, inline create) -> Save (imageUpdate performer_ids).
//
// Shell (filter modal / sort / per-page / pager) adapted from stash-auto-vision-tagging's
// tag-manager.js; the per-image Scrape->Select->Save row is the net-new piece.
// See docs/IMAGE_TAGGER_FEASIBILITY.md.
(function () {
  "use strict";

  // =========================================================================
  // Constants
  // =========================================================================

  var ROUTE = "/plugins/stash-performer-id";
  var STORAGE_PREFIX = "spid.tagger.";
  // Must match the installed scraper yml filename stem (scraper/stash-performer-id.yml).
  var SCRAPER_ID = "stash-performer-id";
  // stash_ids endpoint for the durable name-record link (DESIGN §8). Stable identifier so
  // re-scrapes resolve to the same performer.
  var ENDPOINT = "stash-performer-id";

  var SORT_OPTIONS = [
    { value: "path", label: "Path" },
    { value: "title", label: "Title" },
    { value: "date", label: "Date" },
    { value: "created_at", label: "Created At" },
    { value: "updated_at", label: "Updated At" },
    { value: "random", label: "Random" },
  ];
  var PER_PAGE_OPTIONS = [20, 40, 60, 120, 250];

  // =========================================================================
  // API references
  // =========================================================================

  var api = window.PluginApi;
  var React = api.React;
  var el = React.createElement;
  var GQL = api.GQL;

  var useState = React.useState;
  var useMemo = React.useMemo;
  var useCallback = React.useCallback;
  var useRef = React.useRef;

  var Bootstrap = api.libraries.Bootstrap;
  var Button = Bootstrap.Button;
  var Badge = Bootstrap.Badge;
  var Modal = Bootstrap.Modal;
  var Form = Bootstrap.Form;
  var Dropdown = Bootstrap.Dropdown;
  var Spinner = Bootstrap.Spinner;
  var Accordion = Bootstrap.Accordion;
  var Card = Bootstrap.Card;

  var NavLink = api.libraries.ReactRouterDOM.NavLink;
  var Link = api.libraries.ReactRouterDOM.Link;
  var FA = api.libraries.FontAwesomeSolid;

  // =========================================================================
  // Imperative GraphQL (scrape / resolve / create / associate) via csLib
  // =========================================================================

  function gql(query, variables) {
    // csLib.callGQL resolves to response.data (undefined on GraphQL error).
    return window.csLib.callGQL({ query: query, variables: variables });
  }

  function scrapeImage(imageId) {
    return gql(
      "query ($source: ScraperSourceInput!, $input: ScrapeSingleImageInput!) {" +
        " scrapeSingleImage(source: $source, input: $input) {" +
        " performers { name disambiguation stored_id remote_site_id } } }",
      { source: { scraper_id: SCRAPER_ID }, input: { image_id: String(imageId) } }
    ).then(function (d) {
      var results = d && d.scrapeSingleImage;
      if (!results || !results.length) return null;
      var perfs = results[0].performers || [];
      return perfs.length ? perfs[0] : null;
    });
  }

  function findPerformers(filter) {
    return gql(
      "query ($f: PerformerFilterType) { findPerformers(performer_filter: $f, filter:" +
        " {per_page: 1}) { performers { id name disambiguation } } }",
      { f: filter }
    ).then(function (d) {
      var list = d && d.findPerformers && d.findPerformers.performers;
      return list && list.length ? list[0] : null;
    });
  }

  function findPerformer(id) {
    return gql("query ($id: ID!) { findPerformer(id: $id) { id name disambiguation } }", {
      id: String(id),
    }).then(function (d) {
      return (d && d.findPerformer) || null;
    });
  }

  // Resolve a scraped performer to an existing local one: stored_id -> stash_id -> name.
  function resolveScraped(scraped) {
    if (scraped.stored_id) {
      return findPerformer(scraped.stored_id);
    }
    var byStashId = scraped.remote_site_id
      ? findPerformers({
          stash_id_endpoint: {
            endpoint: ENDPOINT,
            stash_id: String(scraped.remote_site_id),
            modifier: "EQUALS",
          },
        })
      : Promise.resolve(null);
    return byStashId.then(function (p) {
      if (p) return p;
      if (!scraped.name) return null;
      return findPerformers({ name: { value: scraped.name, modifier: "EQUALS" } });
    });
  }

  function createPerformer(scraped) {
    var input = { name: scraped.name };
    if (scraped.disambiguation) input.disambiguation = scraped.disambiguation;
    if (scraped.remote_site_id) {
      input.stash_ids = [{ endpoint: ENDPOINT, stash_id: String(scraped.remote_site_id) }];
    }
    return gql(
      "mutation ($input: PerformerCreateInput!) { performerCreate(input: $input) {" +
        " id name disambiguation } }",
      { input: input }
    ).then(function (d) {
      return (d && d.performerCreate) || null;
    });
  }

  function associate(imageId, performerIds) {
    return gql(
      "mutation ($input: ImageUpdateInput!) { imageUpdate(input: $input) {" +
        " id performers { id name } } }",
      { input: { id: String(imageId), performer_ids: performerIds } }
    ).then(function (d) {
      return (d && d.imageUpdate) || null;
    });
  }

  // Add one performer to many images in a single call (merge-add; idempotent).
  function bulkAssociate(imageIds, performerId) {
    return gql(
      "mutation ($input: BulkImageUpdateInput!) { bulkImageUpdate(input: $input) { id } }",
      {
        input: {
          ids: imageIds.map(String),
          performer_ids: { ids: [String(performerId)], mode: "ADD" },
        },
      }
    ).then(function (d) {
      return (d && d.bulkImageUpdate) || null;
    });
  }

  // =========================================================================
  // Small utilities (view state persistence, tag value shims) — from tag-manager.js
  // =========================================================================

  function loadSetting(key, fallback) {
    try {
      var raw = localStorage.getItem(STORAGE_PREFIX + key);
      return raw !== null ? JSON.parse(raw) : fallback;
    } catch (_) {
      return fallback;
    }
  }
  function saveSetting(key, value) {
    try {
      localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(value));
    } catch (_) {
      /* quota — ignore */
    }
  }
  function useLocalStorage(key, fallback) {
    var pair = useState(function () {
      return loadSetting(key, fallback);
    });
    React.useEffect(
      function () {
        saveSetting(key, pair[0]);
      },
      [key, pair[0]]
    );
    return pair;
  }

  function useAllTags() {
    var result = GQL.useFindTagsQuery({
      variables: { filter: { per_page: -1, sort: "name", direction: GQL.SortDirectionEnum.Asc } },
    });
    return useMemo(
      function () {
        if (!result.data || !result.data.findTags) return [];
        return result.data.findTags.tags;
      },
      [result.data]
    );
  }

  function idsToTagValues(ids, allTags) {
    var map = {};
    allTags.forEach(function (t) {
      map[t.id] = t;
    });
    return ids.map(function (id) {
      return map[id] || { id: id, name: id };
    });
  }

  function formatDate(dateStr) {
    if (!dateStr) return "";
    try {
      return new Date(dateStr).toLocaleDateString();
    } catch (_) {
      return dateStr;
    }
  }

  // =========================================================================
  // TagIncludeExcludeControl — unified [+]/[-] tag picker (from tag-manager.js)
  // =========================================================================

  function TagIncludeExcludeControl(props) {
    var allTags = props.allTags;
    var includedIds = props.includedIds;
    var excludedIds = props.excludedIds;
    var onIncludedChange = props.onIncludedChange;
    var onExcludedChange = props.onExcludedChange;

    var searchState = useState("");
    var search = searchState[0];
    var setSearch = searchState[1];

    var includedSet = useMemo(
      function () {
        return new Set(includedIds);
      },
      [includedIds]
    );
    var excludedSet = useMemo(
      function () {
        return new Set(excludedIds);
      },
      [excludedIds]
    );

    var filteredTags = useMemo(
      function () {
        if (!search) return allTags;
        var lower = search.toLowerCase();
        return allTags.filter(function (t) {
          return t.name.toLowerCase().indexOf(lower) >= 0;
        });
      },
      [allTags, search]
    );

    var tagNameMap = useMemo(
      function () {
        var m = {};
        allTags.forEach(function (t) {
          m[t.id] = t.name;
        });
        return m;
      },
      [allTags]
    );

    function toggleInclude(id) {
      if (includedSet.has(id)) {
        onIncludedChange(includedIds.filter((x) => x !== id));
      } else {
        if (excludedSet.has(id)) onExcludedChange(excludedIds.filter((x) => x !== id));
        onIncludedChange(includedIds.concat(id));
      }
    }
    function toggleExclude(id) {
      if (excludedSet.has(id)) {
        onExcludedChange(excludedIds.filter((x) => x !== id));
      } else {
        if (includedSet.has(id)) onIncludedChange(includedIds.filter((x) => x !== id));
        onExcludedChange(excludedIds.concat(id));
      }
    }
    function removePill(id) {
      if (includedSet.has(id)) onIncludedChange(includedIds.filter((x) => x !== id));
      else if (excludedSet.has(id)) onExcludedChange(excludedIds.filter((x) => x !== id));
    }

    var pills = [];
    includedIds.forEach(function (id) {
      pills.push(
        el(
          Badge,
          {
            key: "i-" + id,
            className: "spid-tag-included",
            onClick: function () {
              removePill(id);
            },
            style: { cursor: "pointer" },
          },
          (tagNameMap[id] || id) + " ×"
        )
      );
    });
    excludedIds.forEach(function (id) {
      pills.push(
        el(
          Badge,
          {
            key: "e-" + id,
            className: "spid-tag-excluded",
            onClick: function () {
              removePill(id);
            },
            style: { cursor: "pointer" },
          },
          (tagNameMap[id] || id) + " ×"
        )
      );
    });

    return el(
      "div",
      null,
      pills.length > 0 && el("div", { className: "spid-tag-pills" }, pills),
      el(
        "div",
        { className: "spid-tag-control" },
        el(
          "div",
          { className: "spid-tag-control-search" },
          el(Form.Control, {
            className: "btn-secondary",
            placeholder: "Search…",
            value: search,
            onChange: function (e) {
              setSearch(e.target.value);
            },
          })
        ),
        filteredTags.map(function (tag) {
          var isIncluded = includedSet.has(tag.id);
          var isExcluded = excludedSet.has(tag.id);
          return el(
            "div",
            { key: tag.id, className: "spid-tag-row" },
            el(
              Button,
              {
                variant: "link",
                size: "sm",
                className: "spid-btn-include" + (isIncluded ? " active" : ""),
                onClick: function () {
                  toggleInclude(tag.id);
                },
                title: "Include",
              },
              "+"
            ),
            el(
              "span",
              {
                className:
                  "spid-tag-name" +
                  (isIncluded ? " text-success" : isExcluded ? " text-danger" : ""),
              },
              tag.name
            ),
            el(
              Button,
              {
                variant: "link",
                size: "sm",
                className: "spid-btn-exclude" + (isExcluded ? " active" : ""),
                onClick: function () {
                  toggleExclude(tag.id);
                },
                title: "Exclude",
              },
              "−"
            )
          );
        })
      )
    );
  }

  // =========================================================================
  // EditFilterModal — Tags / Path / Organized (adapted from tag-manager.js)
  // =========================================================================

  function EditFilterModal(props) {
    var allTags = props.allTags;
    var Icon = api.components.Icon;

    var openSectionState = useState("tags");
    var openSection = openSectionState[0];
    var setOpenSection = openSectionState[1];

    var hasAnyFilter =
      props.filterTagIncludeIds.length > 0 ||
      props.filterTagExcludeIds.length > 0 ||
      props.filterPath ||
      props.filterOrganized !== null;

    function handleClear() {
      props.onFilterTagIncludeChange([]);
      props.onFilterTagExcludeChange([]);
      props.onFilterTagDepthChange(0);
      props.onFilterPathChange("");
      props.onFilterOrganizedChange(null);
    }
    function clearSection(key) {
      if (key === "tags") {
        props.onFilterTagIncludeChange([]);
        props.onFilterTagExcludeChange([]);
        props.onFilterTagDepthChange(0);
      }
      if (key === "path") props.onFilterPathChange("");
      if (key === "organized") props.onFilterOrganizedChange(null);
    }

    function filterCard(key, label, hasValue, body) {
      return el(
        Card,
        { key: key },
        el(
          Accordion.Toggle,
          { eventKey: key, className: "spid-filter-header" },
          el(
            "span",
            { className: "mr-auto" },
            el(Icon, {
              className: "fa-fw",
              icon: openSection === key ? FA.faChevronDown : FA.faChevronRight,
            }),
            " " + label
          ),
          hasValue &&
            el(
              Button,
              {
                variant: "minimal",
                onClick: function (e) {
                  e.stopPropagation();
                  clearSection(key);
                },
              },
              el(Icon, { icon: FA.faTimes })
            )
        ),
        el(Accordion.Collapse, { eventKey: key }, el(Card.Body, null, body))
      );
    }

    return el(
      Modal,
      { show: props.show, onHide: props.onClose, className: "spid-filter-dialog" },
      el(Modal.Header, null, el("div", null, "Edit Filter")),
      el(
        Modal.Body,
        null,
        el(
          Accordion,
          {
            activeKey: openSection,
            onSelect: function (k) {
              setOpenSection(k === openSection ? null : k);
            },
          },
          filterCard(
            "tags",
            "Tags",
            props.filterTagIncludeIds.length + props.filterTagExcludeIds.length > 0,
            el(
              "div",
              null,
              el(Form.Check, {
                type: "checkbox",
                label: "Include sub-tags",
                checked: props.filterTagDepth === -1,
                onChange: function (e) {
                  props.onFilterTagDepthChange(e.target.checked ? -1 : 0);
                },
                id: "spid-filter-tag-depth",
                className: "mb-2",
              }),
              el(TagIncludeExcludeControl, {
                allTags: allTags,
                includedIds: props.filterTagIncludeIds,
                excludedIds: props.filterTagExcludeIds,
                onIncludedChange: props.onFilterTagIncludeChange,
                onExcludedChange: props.onFilterTagExcludeChange,
              })
            )
          ),
          filterCard(
            "path",
            "Path",
            !!props.filterPath,
            el(
              "div",
              null,
              el(
                Form.Group,
                { className: "spid-modifier-options" },
                [GQL.CriterionModifier.Includes, GQL.CriterionModifier.MatchesRegex].map(
                  function (m) {
                    var label = m === GQL.CriterionModifier.Includes ? "includes" : "matches regex";
                    return el(
                      Button,
                      {
                        key: m,
                        variant: props.filterPathModifier === m ? "primary" : "secondary",
                        size: "sm",
                        className: "mr-2",
                        onClick: function () {
                          props.onFilterPathModifierChange(m);
                        },
                      },
                      label
                    );
                  }
                )
              ),
              el(Form.Control, {
                className: "btn-secondary",
                placeholder: "File path…",
                value: props.filterPath,
                onChange: function (e) {
                  props.onFilterPathChange(e.target.value);
                },
              })
            )
          ),
          filterCard(
            "organized",
            "Organized",
            props.filterOrganized !== null,
            el(
              "div",
              null,
              [
                { label: "Any", value: null },
                { label: "Organized", value: true },
                { label: "Not Organized", value: false },
              ].map(function (opt, i) {
                return el(Form.Check, {
                  key: i,
                  type: "radio",
                  label: opt.label,
                  name: "spid-org",
                  checked: props.filterOrganized === opt.value,
                  onChange: function () {
                    props.onFilterOrganizedChange(opt.value);
                  },
                  id: "spid-org-" + i,
                });
              })
            )
          )
        )
      ),
      el(
        Modal.Footer,
        null,
        hasAnyFilter && el(Button, { variant: "secondary", onClick: handleClear }, "Clear All"),
        el(Button, { onClick: props.onClose }, "Close")
      )
    );
  }

  // =========================================================================
  // SortControl / PerPageSelect / PaginationNav (from tag-manager.js)
  // =========================================================================

  function SortControl(props) {
    var Icon = api.components.Icon;
    return el(
      Dropdown,
      { as: Bootstrap.ButtonGroup, className: "sort-by-select" },
      el(
        Dropdown.Toggle,
        { variant: "secondary" },
        (
          SORT_OPTIONS.find(function (o) {
            return o.value === props.sortField;
          }) || {}
        ).label || props.sortField
      ),
      el(
        Dropdown.Menu,
        { className: "bg-secondary text-white" },
        SORT_OPTIONS.map(function (opt) {
          return el(
            Dropdown.Item,
            {
              key: opt.value,
              active: opt.value === props.sortField,
              className: "bg-secondary text-white",
              onClick: function () {
                props.onSortFieldChange(opt.value);
              },
            },
            opt.label
          );
        })
      ),
      el(
        Button,
        {
          variant: "secondary",
          onClick: props.onSortDirToggle,
          title: props.sortDir === "DESC" ? "Descending" : "Ascending",
        },
        el(Icon, { icon: props.sortDir === "DESC" ? FA.faSortAmountDown : FA.faSortAmountUp })
      )
    );
  }

  function PerPageSelect(props) {
    return el(
      Form.Control,
      {
        as: "select",
        className: "btn-secondary page-size-selector",
        value: props.value,
        onChange: function (e) {
          props.onChange(Number(e.target.value));
        },
      },
      PER_PAGE_OPTIONS.map(function (n) {
        return el("option", { key: n, value: n }, n);
      })
    );
  }

  function PaginationNav(props) {
    var currentPage = props.currentPage;
    var totalPages = props.totalPages;
    var totalItems = props.totalItems;
    var perPage = props.perPage;
    if (totalPages <= 1 && !totalItems) return null;

    var countText = null;
    if (totalItems != null) {
      var start = totalItems === 0 ? 0 : (currentPage - 1) * perPage + 1;
      var end = Math.min(currentPage * perPage, totalItems);
      countText = el(
        "span",
        { className: "filter-container text-muted paginationIndex center-text" },
        start + "-" + end + " of " + totalItems
      );
    }
    function navBtn(label, page, disabled) {
      return el(
        Button,
        {
          key: label,
          variant: "secondary",
          disabled: disabled,
          onClick: function () {
            props.onPageChange(page);
          },
        },
        label
      );
    }
    return el(
      "div",
      { className: "pagination-index-container" },
      totalPages > 1 &&
        el(
          "div",
          { className: "pagination btn-group" },
          navBtn("«", 1, currentPage === 1),
          navBtn("‹", currentPage - 1, currentPage === 1),
          el(
            "div",
            { className: "page-count-container" },
            el(
              "div",
              { className: "btn-group" },
              el(Button, { variant: "secondary" }, currentPage + " of " + totalPages)
            )
          ),
          navBtn("›", currentPage + 1, currentPage === totalPages),
          navBtn("»", totalPages, currentPage === totalPages)
        ),
      countText
    );
  }

  // =========================================================================
  // ImageCard — a scene-tagger-style card: header (thumb / title / current /
  // Scrape) with a Collapse slidedown for the assignment (suggestion + picker).
  // =========================================================================

  function ImageCard(props) {
    var image = props.image;
    var st = props.rowState || {}; // { scraped, selected, status, message, action, open }
    var PerformerSelect = api.components.PerformerSelect;
    var Icon = api.components.Icon;
    var Collapse = Bootstrap.Collapse;

    var filePath = image.visual_files && image.visual_files[0] ? image.visual_files[0].path : "";
    var thumb = image.paths && image.paths.thumbnail;
    var current = image.performers || [];
    var scraped = st.scraped;
    var selected = st.selected;
    var busy = st.status === "busy";
    var open = !!st.open;

    // --- assignment body (shown in the slidedown once scraped) ---
    function renderBody() {
      var body = [
        el(
          "span",
          { key: "sg", className: "spid-suggestion mr-3" },
          "Suggested: ",
          el("b", null, scraped.name || "(no match)")
        ),
      ];
      if (PerformerSelect) {
        body.push(
          el(
            "div",
            { key: "ps", className: "spid-perf-select mr-2" },
            el(PerformerSelect, {
              isMulti: false,
              isClearable: true,
              values: selected ? [selected] : [],
              onSelect: function (items) {
                props.onSelect(image, items && items.length ? items[0] : null);
              },
            })
          )
        );
      }
      if (!selected && scraped.name) {
        body.push(
          el(
            Button,
            {
              key: "cr",
              variant: "outline-success",
              size: "sm",
              className: "mr-2",
              disabled: busy,
              onClick: function () {
                props.onCreate(image);
              },
            },
            'Create "' + scraped.name + '"'
          )
        );
      }
      body.push(
        el(
          Button,
          {
            key: "sv",
            variant: "primary",
            size: "sm",
            disabled: busy || !selected,
            onClick: function () {
              props.onSave(image);
            },
          },
          busy && st.action === "save" ? el(Spinner, { animation: "border", size: "sm" }) : "Save"
        )
      );
      if (st.status === "error") {
        body.push(
          el(
            "span",
            { key: "er", className: "spid-status text-danger ml-2", title: st.message },
            "! " + (st.message || "error")
          )
        );
      }
      return body;
    }

    return el(
      "div",
      { className: "search-item spid-card" },
      // --- header row ---
      el(
        "div",
        { className: "spid-card-head d-flex align-items-center" },
        el(
          "label",
          { className: "spid-card-check mb-0 mr-3" },
          el("input", {
            type: "checkbox",
            checked: !!props.checked,
            onChange: function () {
              props.onToggle(image.id);
            },
          })
        ),
        el(
          Link,
          { to: "/images/" + image.id, className: "spid-card-thumb mr-3" },
          thumb ? el("img", { className: "image-thumbnail", src: thumb, loading: "lazy" }) : null
        ),
        el(
          "div",
          { className: "spid-card-info flex-grow-1 overflow-hidden" },
          el(
            "div",
            { className: "spid-card-title ellips-data" },
            image.title || (filePath ? filePath.split("/").pop() : image.id)
          ),
          filePath && el("div", { className: "spid-card-path text-muted" }, filePath),
          el(
            "div",
            { className: "spid-card-current mt-1" },
            current.length
              ? current.map(function (p) {
                  return el(Badge, { key: p.id, className: "spid-perf-badge" }, p.name);
                })
              : el("span", { className: "text-muted small" }, "No performers")
          )
        ),
        el(
          "div",
          { className: "spid-card-actions d-flex align-items-center" },
          st.status === "saved" && el("span", { className: "text-success mr-2" }, "✓ saved"),
          el(
            Button,
            {
              variant: "secondary",
              size: "sm",
              disabled: busy,
              onClick: function () {
                props.onScrape(image);
              },
            },
            busy && st.action === "scrape"
              ? el(Spinner, { animation: "border", size: "sm" })
              : "Scrape"
          ),
          scraped &&
            el(
              Button,
              {
                variant: "minimal",
                className: "spid-collapse-btn ml-1",
                title: open ? "Collapse" : "Expand",
                onClick: function () {
                  props.onToggleOpen(image.id);
                },
              },
              el(Icon, { icon: open ? FA.faChevronUp : FA.faChevronDown })
            )
        )
      ),
      // --- slidedown assignment ---
      el(
        Collapse,
        { in: open },
        el(
          "div",
          null,
          el(
            "div",
            { className: "spid-card-body d-flex align-items-center flex-wrap" },
            scraped ? renderBody() : null
          )
        )
      )
    );
  }

  // =========================================================================
  // ImageTaggerPageInner
  // =========================================================================

  function ImageTaggerPageInner() {
    var pageState = useState(1);
    var page = pageState[0];
    var setPage = pageState[1];

    var perPageLS = useLocalStorage("perPage", 40);
    var perPage = perPageLS[0];
    var setPerPage = perPageLS[1];
    var sortFieldLS = useLocalStorage("sortField", "path");
    var sortField = sortFieldLS[0];
    var setSortField = sortFieldLS[1];
    var sortDirLS = useLocalStorage("sortDir", "ASC");
    var sortDir = sortDirLS[0];
    var setSortDir = sortDirLS[1];

    var allTags = useAllTags();
    var toast = api.hooks.useToast();

    // Per-row tagger state, keyed by image id.
    var rowStateS = useState({});
    var rowState = rowStateS[0];
    var setRowState = rowStateS[1];

    function patchRow(id, patch) {
      setRowState(function (prev) {
        var next = Object.assign({}, prev);
        next[id] = Object.assign({}, prev[id], patch);
        return next;
      });
    }

    // Row selection (checkboxes) + batch progress.
    var selS = useState(function () {
      return new Set();
    });
    var selected = selS[0];
    var setSelected = selS[1];
    var progS = useState(null); // null | { done, total, saving? }
    var progress = progS[0];
    var setProgress = progS[1];
    var stoppingRef = useRef(false);

    // Filters
    var filterOpenS = useState(false);
    var filterOpen = filterOpenS[0];
    var setFilterOpen = filterOpenS[1];
    var tagIncS = useState([]);
    var tagExcS = useState([]);
    var tagDepthS = useState(0);
    var pathS = useState("");
    var pathModS = useState(GQL.CriterionModifier.Includes);
    var orgS = useState(null);

    var filterTagIncludeIds = tagIncS[0];
    var filterTagExcludeIds = tagExcS[0];
    var filterTagDepth = tagDepthS[0];
    var filterPath = pathS[0];
    var filterPathModifier = pathModS[0];
    var filterOrganized = orgS[0];

    var direction = sortDir === "DESC" ? GQL.SortDirectionEnum.Desc : GQL.SortDirectionEnum.Asc;

    var imageFilter = useMemo(
      function () {
        var f = {};
        var has = false;
        if (filterTagIncludeIds.length > 0) {
          f.tags = {
            value: filterTagIncludeIds,
            modifier: GQL.CriterionModifier.IncludesAll,
            depth: filterTagDepth,
          };
          has = true;
        }
        if (filterTagExcludeIds.length > 0) {
          var exc = {
            value: filterTagExcludeIds,
            modifier: GQL.CriterionModifier.Excludes,
            depth: filterTagDepth,
          };
          if (f.tags) f.AND = { tags: exc };
          else f.tags = exc;
          has = true;
        }
        if (filterPath) {
          f.path = { value: filterPath, modifier: filterPathModifier };
          has = true;
        }
        if (filterOrganized !== null) {
          f.organized = filterOrganized;
          has = true;
        }
        return has ? f : undefined;
      },
      [
        filterTagIncludeIds,
        filterTagExcludeIds,
        filterTagDepth,
        filterPath,
        filterPathModifier,
        filterOrganized,
      ]
    );

    var queryResult = GQL.useFindImagesQuery({
      variables: {
        filter: { page: page, per_page: perPage, sort: sortField, direction: direction },
        image_filter: imageFilter,
      },
      fetchPolicy: "cache-and-network",
    });

    var data = queryResult.data;
    var loading = queryResult.loading;
    var images = data && data.findImages ? data.findImages.images : [];
    var totalCount = data && data.findImages ? data.findImages.count : 0;
    var totalPages = Math.ceil(totalCount / perPage) || 1;

    // Reset selection when the visible page/filter changes.
    React.useEffect(
      function () {
        setSelected(new Set());
      },
      [page, perPage, sortField, sortDir, imageFilter]
    );

    // --- row handlers ---

    // Scrape one image: fetch the suggestion and auto-resolve to an existing performer.
    // Returns a promise (reused by the batch loop).
    function scrapeOne(image) {
      patchRow(image.id, { status: "busy", action: "scrape", message: null });
      return scrapeImage(image.id)
        .then(function (scraped) {
          if (!scraped) {
            patchRow(image.id, {
              status: "idle",
              action: null,
              scraped: { name: null },
              selected: null,
              open: true,
            });
            return;
          }
          return resolveScraped(scraped).then(function (match) {
            patchRow(image.id, {
              status: "idle",
              action: null,
              scraped: scraped,
              selected: match || null,
              open: true,
            });
          });
        })
        .catch(function (e) {
          patchRow(image.id, { status: "error", action: null, message: String(e) });
        });
    }

    function onScrape(image) {
      scrapeOne(image);
    }

    function onSelect(image, performer) {
      patchRow(image.id, { selected: performer, status: "idle" });
    }

    function onToggleOpen(id) {
      var st = rowState[id];
      patchRow(id, { open: !(st && st.open) });
    }

    function onCreate(image) {
      var st = rowState[image.id];
      var scraped = st && st.scraped;
      if (!scraped || !scraped.name) return;
      patchRow(image.id, { status: "busy", action: "create" });
      createPerformer(scraped)
        .then(function (p) {
          if (p) patchRow(image.id, { selected: p, status: "idle", action: null });
          else patchRow(image.id, { status: "error", action: null, message: "performerCreate failed" });
        })
        .catch(function (e) {
          patchRow(image.id, { status: "error", action: null, message: String(e) });
        });
    }

    // Merge one image's selected performer (returns a promise).
    function saveOne(image, sel) {
      var existing = (image.performers || []).map(function (p) {
        return p.id;
      });
      var ids = existing.slice();
      if (ids.indexOf(sel.id) < 0) ids.push(sel.id);
      patchRow(image.id, { status: "busy", action: "save" });
      return associate(image.id, ids)
        .then(function (updated) {
          if (updated) patchRow(image.id, { status: "saved", action: null });
          else patchRow(image.id, { status: "error", action: null, message: "imageUpdate failed" });
        })
        .catch(function (e) {
          patchRow(image.id, { status: "error", action: null, message: String(e) });
        });
    }

    function onSave(image) {
      var st = rowState[image.id];
      if (!st || !st.selected) return;
      var sel = st.selected;
      saveOne(image, sel).then(function () {
        toast.toast({ content: "Saved " + (sel.name || "performer") });
        queryResult.refetch();
      });
    }

    // --- selection ---
    function toggleRow(id) {
      setSelected(function (prev) {
        var n = new Set(prev);
        if (n.has(id)) n.delete(id);
        else n.add(id);
        return n;
      });
    }
    function toggleAll() {
      setSelected(function (prev) {
        var all = images.length > 0 && images.every(function (im) {
          return prev.has(im.id);
        });
        if (all) return new Set();
        var n = new Set();
        images.forEach(function (im) {
          n.add(im.id);
        });
        return n;
      });
    }
    // Batch target: checked rows on this page, or the whole page when nothing is checked.
    function targetImages() {
      if (selected.size === 0) return images;
      return images.filter(function (im) {
        return selected.has(im.id);
      });
    }

    // --- batch scrape (sequential + cancellable — SceneTagger's method) ---
    function onScrapeBatch() {
      var targets = targetImages();
      if (!targets.length || progress) return;
      stoppingRef.current = false;
      setProgress({ done: 0, total: targets.length });
      targets
        .reduce(function (p, image) {
          return p.then(function () {
            if (stoppingRef.current) return;
            return scrapeOne(image).then(function () {
              setProgress(function (pr) {
                return pr ? { done: pr.done + 1, total: pr.total } : pr;
              });
            });
          });
        }, Promise.resolve())
        .then(function () {
          setProgress(null);
        });
    }
    function onStop() {
      stoppingRef.current = true;
    }

    // A row is savable once scraped: either a resolved existing performer, or a new name to create.
    function rowTarget(im) {
      var st = rowState[im.id];
      if (!st) return null;
      if (st.selected) {
        return { key: "id:" + st.selected.id, id: st.selected.id, scraped: null, imageId: im.id };
      }
      if (st.scraped && st.scraped.name) {
        var k = st.scraped.remote_site_id || st.scraped.name;
        return { key: "new:" + k, id: null, scraped: st.scraped, imageId: im.id };
      }
      return null;
    }

    // --- batch save: group by performer, create each distinct new one once, then bulk-associate ---
    function onSaveBatch() {
      var rows = targetImages()
        .map(rowTarget)
        .filter(Boolean);
      if (!rows.length || progress) return;
      var groups = {};
      rows.forEach(function (r) {
        var g = groups[r.key] || (groups[r.key] = { id: r.id, scraped: r.scraped, imageIds: [] });
        g.imageIds.push(r.imageId);
      });
      setProgress({ done: 0, total: rows.length, saving: true });
      Object.keys(groups)
        .reduce(function (p, key) {
          return p.then(function () {
            var g = groups[key];
            var idP = g.id
              ? Promise.resolve(g.id)
              : createPerformer(g.scraped).then(function (perf) {
                  return perf ? perf.id : null;
                });
            return idP
              .then(function (pid) {
                if (!pid) {
                  g.imageIds.forEach(function (id) {
                    patchRow(id, { status: "error", message: "performerCreate failed" });
                  });
                  return;
                }
                return bulkAssociate(g.imageIds, pid).then(function () {
                  g.imageIds.forEach(function (id) {
                    patchRow(id, { status: "saved", action: null });
                  });
                });
              })
              .then(function () {
                setProgress(function (pr) {
                  return pr
                    ? { done: pr.done + g.imageIds.length, total: pr.total, saving: true }
                    : pr;
                });
              });
          });
        }, Promise.resolve())
        .then(function () {
          setProgress(null);
          toast.toast({ content: "Saved " + rows.length + " image(s)" });
          queryResult.refetch();
        })
        .catch(function (e) {
          setProgress(null);
          toast.toast({ variant: "danger", content: "Batch save failed: " + e });
        });
    }

    // Counts for the toolbar batch buttons.
    var scopeCount = selected.size || images.length;
    var savableCount = targetImages().filter(function (im) {
      return rowTarget(im) !== null;
    }).length;
    var allSelected =
      images.length > 0 &&
      images.every(function (im) {
        return selected.has(im.id);
      });

    // --- toolbar handlers ---
    function resetToFirst(fn) {
      setPage(1);
      fn();
    }

    var hasActiveFilter = imageFilter != null;

    return el(
      "div",
      { className: "spid-page tagger-container mx-auto" },

      // View controls (native list toolbar classes)
      el(
        "div",
        { className: "filtered-list-toolbar btn-toolbar", role: "toolbar" },
        el(
          Button,
          {
            variant: hasActiveFilter ? "primary" : "secondary",
            className: "mr-2",
            onClick: function () {
              setFilterOpen(!filterOpen);
            },
            title: "Edit Filter",
          },
          el(api.components.Icon, { icon: FA.faFilter }),
          " Filter"
        ),
        el(SortControl, {
          sortField: sortField,
          sortDir: sortDir,
          onSortFieldChange: function (f) {
            resetToFirst(function () {
              setSortField(f);
            });
          },
          onSortDirToggle: function () {
            resetToFirst(function () {
              setSortDir(sortDir === "DESC" ? "ASC" : "DESC");
            });
          },
        }),
        el(PerPageSelect, {
          value: perPage,
          onChange: function (n) {
            resetToFirst(function () {
              setPerPage(n);
            });
          },
        }),
        el(
          Button,
          {
            variant: "secondary",
            onClick: function () {
              queryResult.refetch();
            },
            title: "Refresh",
          },
          el(api.components.Icon, { icon: FA.faSync })
        )
      ),

      // Batch CTA row (scene-tagger style): select-all on the left, Scrape/Save on the right.
      // They act on the checked rows, or the whole page when nothing is checked.
      el(
        "div",
        {
          className:
            "spid-cta-row tagger-container-header d-flex justify-content-between" +
            " align-items-center flex-wrap",
        },
        el(
          "label",
          { className: "spid-selectall mb-0" },
          el("input", { type: "checkbox", checked: allSelected, onChange: toggleAll }),
          el(
            "span",
            { className: "ml-2" },
            selected.size ? selected.size + " selected" : "Select all"
          )
        ),
        el(
          "div",
          { className: "d-flex align-items-center" },
          progress
            ? el(
                React.Fragment,
                null,
                !progress.saving &&
                  el(
                    Button,
                    { variant: "danger", onClick: onStop },
                    el(api.components.Icon, { icon: FA.faStop }),
                    " Stop"
                  ),
                el(
                  "span",
                  { className: "text-muted ml-2" },
                  (progress.saving ? "Saving " : "Scraping ") +
                    progress.done +
                    "/" +
                    progress.total
                )
              )
            : el(
                React.Fragment,
                null,
                el(
                  Button,
                  {
                    variant: "secondary",
                    onClick: onScrapeBatch,
                    disabled: images.length === 0,
                    title:
                      "Scrape " + (selected.size ? selected.size + " selected" : "all on page"),
                  },
                  el(api.components.Icon, { icon: FA.faMagic }),
                  " " + (selected.size ? "Scrape (" + scopeCount + ")" : "Scrape All")
                ),
                el(
                  Button,
                  {
                    variant: "primary",
                    onClick: onSaveBatch,
                    disabled: savableCount === 0,
                    className: "ml-2",
                    title: "Save resolved rows",
                  },
                  el(api.components.Icon, { icon: FA.faSave }),
                  " Save" + (savableCount ? " (" + savableCount + ")" : "")
                )
              )
        )
      ),

      el(EditFilterModal, {
        show: filterOpen,
        allTags: allTags,
        filterTagIncludeIds: filterTagIncludeIds,
        filterTagExcludeIds: filterTagExcludeIds,
        filterTagDepth: filterTagDepth,
        filterPath: filterPath,
        filterPathModifier: filterPathModifier,
        filterOrganized: filterOrganized,
        onFilterTagIncludeChange: function (v) {
          resetToFirst(function () {
            tagIncS[1](v);
          });
        },
        onFilterTagExcludeChange: function (v) {
          resetToFirst(function () {
            tagExcS[1](v);
          });
        },
        onFilterTagDepthChange: tagDepthS[1],
        onFilterPathChange: function (v) {
          resetToFirst(function () {
            pathS[1](v);
          });
        },
        onFilterPathModifierChange: pathModS[1],
        onFilterOrganizedChange: function (v) {
          resetToFirst(function () {
            orgS[1](v);
          });
        },
        onClose: function () {
          setFilterOpen(false);
        },
      }),

      el(PaginationNav, {
        currentPage: page,
        totalPages: totalPages,
        totalItems: totalCount,
        perPage: perPage,
        onPageChange: setPage,
      }),

      loading && !data
        ? el(
            "div",
            { className: "text-center py-4" },
            el(Spinner, { animation: "border" })
          )
        : images.length === 0
          ? el(
              "div",
              { className: "text-center text-muted py-5" },
              el("h5", null, "No images found"),
              hasActiveFilter && el("p", null, "Try adjusting the filter.")
            )
          : el(
              "div",
              { className: "spid-card-list" },
              images.map(function (image) {
                return el(ImageCard, {
                  key: image.id,
                  image: image,
                  rowState: rowState[image.id],
                  checked: selected.has(image.id),
                  onToggle: toggleRow,
                  onToggleOpen: onToggleOpen,
                  onScrape: onScrape,
                  onSelect: onSelect,
                  onCreate: onCreate,
                  onSave: onSave,
                });
              })
            ),

      el(PaginationNav, {
        currentPage: page,
        totalPages: totalPages,
        totalItems: totalCount,
        perPage: perPage,
        onPageChange: setPage,
      })
    );
  }

  // =========================================================================
  // Loadable gate (PerformerSelect is a loadable component)
  // =========================================================================

  function ImageTaggerPage() {
    var loadingComponents = api.hooks.useLoadComponents([
      api.loadableComponents.PerformerSelect,
    ]);
    if (loadingComponents) {
      return el(
        "div",
        { className: "text-center py-5" },
        el(api.components.LoadingIndicator, null)
      );
    }
    return el(ImageTaggerPageInner, null);
  }

  // =========================================================================
  // Registration
  // =========================================================================

  api.register.route(ROUTE, ImageTaggerPage);

  api.patch.before("MainNavBar.UtilityItems", function (props) {
    var Icon = api.components.Icon;
    return [
      {
        children: el(
          React.Fragment,
          null,
          props.children,
          el(
            NavLink,
            { className: "nav-utility", exact: true, to: ROUTE },
            el(
              Button,
              {
                className: "minimal d-flex align-items-center h-100",
                title: "Performer ID — Image Tagger",
              },
              el(Icon, { icon: FA.faUserTag })
            )
          )
        ),
      },
    ];
  });
})();
