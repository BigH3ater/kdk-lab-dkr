"""grocery-planner: weekly meal-plan + shopping-list builder for the Mack House.

Pure logic (scoring, generator, shoplist) has no network imports so it is unit
tested offline. tandoor/vikunja/seed/run hold the live-API code, reused from the
dashboard mealplan sidecar's allauth login pattern.
"""
