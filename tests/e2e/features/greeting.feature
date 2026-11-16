Feature: Greeting visitors
  The gateway greets visitors and the counter keeps track of their visits.
  Both apps run with Dapr sidecars in Kubernetes.

  Scenario: Repeat visits are counted
    Given a new visitor
    When the visitor is greeted twice
    Then the second greeting reports one more visit than the first

  Scenario: An invalid name is rejected
    When a visitor with an empty name is greeted
    Then the response status is 400
    And the error is "invalid_argument"

  Scenario: A greeting is traced through both apps and their sidecars
    Given a new visitor
    When the visitor is greeted with a new trace id
    Then the trace contains spans of the gateway and counter apps and of both Dapr sidecars
