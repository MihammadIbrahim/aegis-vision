import { configure } from "@testing-library/react-native";

// The signed-in provider chain (stored session → providers → queries) takes a few hundred ms.
configure({ asyncUtilTimeout: 5000 });
