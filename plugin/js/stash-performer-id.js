// Stash Performer ID — Step 2 tagger page (UI-only plugin).
// Phase 0: registers a route + nav link rendering a placeholder page.
(function () {
  "use strict";

  var api = window.PluginApi;
  var React = api.React;
  var el = React.createElement;
  var NavLink = api.libraries.ReactRouterDOM.NavLink;
  var Button = api.libraries.Bootstrap.Button;

  var ROUTE = "/plugins/stash-performer-id";

  function TaggerPage() {
    return el(
      "div",
      { className: "stash-performer-id-page m-4" },
      el("h3", null, "Stash Performer ID"),
      el(
        "p",
        { className: "text-muted" },
        "Phase 0 placeholder. The Step 2 tagger (name → performer association) renders here."
      )
    );
  }

  // Standalone page.
  api.register.route(ROUTE, TaggerPage);

  // Nav-bar entry link.
  api.patch.before("MainNavBar.UtilityItems", function (props) {
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
                title: "Stash Performer ID",
              },
              el("span", { className: "spid-nav-label" }, "PID")
            )
          )
        ),
      },
    ];
  });
})();
